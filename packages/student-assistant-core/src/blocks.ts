import type {
  AssistantBlockListItem,
  AssistantBlockNextStep,
  AssistantBlockTableColumn,
  AssistantResponseBlock,
} from "@vv/contracts";
import type {
  RequestClassification,
  StudentAssistantRequestType,
} from "./contracts";
import {
  isActionRequiredSubmissionState,
  submissionStateLabel,
} from "./document-lifecycle";
import type { DerivedStudentState } from "./state";

/**
 * Structured presentation blocks.
 *
 * Markdown was never a real option here: the portal renders an assistant turn as
 * `<p>{content}</p>`, so a model returning `| award | amount |` would show a
 * student literal pipes. Adding a markdown renderer would mean either a parser
 * dependency or hand-rolled sanitisation, and either way the model would be
 * authoring layout -- exactly what the brief rules out.
 *
 * So the model never emits markup at all. It writes the prose; the *shape* of
 * everything else is decided here, deterministically, from the type of the
 * content. Malformed markup is unrepresentable, raw JSON can never reach a
 * student, and a new block type is an additive change.
 *
 * Every block carries `fallbackText`: its own plain-text rendering. That is what
 * makes the contract safely extensible -- a client that has never heard of a
 * block type still renders something true, and the text channel that voice uses
 * is a projection of the same blocks rather than a second authoring path.
 */

export type AssistantTableColumn = AssistantBlockTableColumn;
export type AssistantListItem = AssistantBlockListItem;
export type AssistantNextStep = AssistantBlockNextStep;
export type AssistantBlock = AssistantResponseBlock;

export const assistantBlockTypes = [
  "text",
  "bullet_list",
  "numbered_list",
  "table",
  "next_steps",
] as const;

/** Request types whose right answer is a sentence and nothing else. */
const proseOnlyRequestTypes = new Set<StudentAssistantRequestType>([
  "greeting",
  "capability_overview",
  "unsupported_or_out_of_scope",
  "explain_requirement",
  "aid_requirement_explanation",
]);

/**
 * A table earns its place when several records share the same columns. Below
 * this many rows a table is heavier to read than a sentence, which is why the
 * brief warns against turning every answer into one.
 */
const minimumTableRows = 2;

export interface BuildBlocksInput {
  classification: RequestClassification;
  derived: DerivedStudentState;
  /** The final, already-grounded message. Becomes the leading text block. */
  message: string;
  /** The student's question, normalised. Presentation depends on what was asked. */
  question?: string;
  /** True when a model wrote the message rather than it being assembled. */
  authored: boolean;
}

export function buildResponseBlocks(input: BuildBlocksInput): AssistantBlock[] {
  const { classification, derived, message } = input;
  const requestType = classification.requestType;

  if (!message.trim()) return [];
  if (proseOnlyRequestTypes.has(requestType)) return [textBlock(message)];

  const detail = [
    ...documentBlocks(requestType, derived),
    ...awardBlocks(requestType, derived),
    ...accountBlocks(requestType, derived),
    ...blockerBlocks(requestType, derived),
    ...deadlineBlocks(requestType, derived, classification, input.question ?? ""),
  ];
  const steps = nextStepBlocks(derived);

  if (detail.length === 0 && steps.length === 0) return [textBlock(message)];

  // When structured blocks carry the detail, an assembled message would repeat
  // it back as one long sentence -- the record dump the brief calls out. An
  // authored message is kept: a model's prose is the answer, and the blocks
  // below it are the supporting detail.
  const lead = input.authored ? message : leadSentence(requestType, derived, message);
  return [textBlock(lead), ...detail, ...steps];
}

/** Plain-text projection, so `message` and the blocks can never diverge. */
export function renderBlocksAsText(blocks: readonly AssistantBlock[]): string {
  return blocks
    .map((block) => block.fallbackText)
    .filter((text) => text.length > 0)
    .join("\n\n");
}

function textBlock(text: string): AssistantBlock {
  return { type: "text", text, fallbackText: text };
}

function bulletList(
  title: string | undefined,
  items: AssistantListItem[],
): AssistantBlock {
  return {
    type: "bullet_list",
    ...(title ? { title } : {}),
    items,
    fallbackText: [title, ...items.map((item) => `- ${item.text}`)]
      .filter(Boolean)
      .join("\n"),
  };
}

function table(
  caption: string | undefined,
  columns: AssistantTableColumn[],
  rows: Record<string, string>[],
): AssistantBlock {
  return {
    type: "table",
    ...(caption ? { caption } : {}),
    columns,
    rows,
    fallbackText: [
      caption,
      ...rows.map((row) =>
        columns.map((column) => `${column.label}: ${row[column.key] ?? "—"}`).join(", "),
      ),
    ]
      .filter(Boolean)
      .join("\n"),
  };
}

function nextSteps(items: AssistantNextStep[]): AssistantBlock {
  return {
    type: "next_steps",
    title: "What to do next",
    items,
    fallbackText: [
      "What to do next:",
      ...items.map((item, index) => `${index + 1}. ${item.text}`),
    ].join("\n"),
  };
}

/**
 * Documents are the clearest case for a table: same columns every row, and the
 * distinction the brief insists on -- missing versus under review versus
 * accepted -- is exactly a status column.
 */
function documentBlocks(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
): AssistantBlock[] {
  if (requestType !== "missing_documents" && requestType !== "document_status") {
    return [];
  }
  const states = derived.documentStates;
  if (states.length < minimumTableRows) return [];
  return [
    table(
      undefined,
      [
        { key: "document", label: "Document" },
        { key: "status", label: "Status" },
        { key: "due", label: "Due" },
      ],
      states.map((state) => ({
        document: state.title,
        status: submissionStateLabel(state.submissionState),
        due: formatDate(state.dueAt),
      })),
    ),
  ];
}

/**
 * Awards come from either aid read, whichever the planner chose. Keying the
 * table off only one of them meant "show me all my awards" answered with a
 * sentence when the planner had picked the other.
 */
function awardBlocks(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
): AssistantBlock[] {
  if (!requestType.startsWith("aid_")) return [];
  const summaryAwards = (derived.aidSummary?.awards ?? []).map((award) => ({
    label: award.name,
    type: award.awardType,
    status: award.status,
    amountUsd: award.status === "accepted" ? award.acceptedUsd : award.offeredUsd,
  }));
  const checklistAwards = (derived.financialAid?.awardAcceptanceStatuses ?? []).map(
    (award) => ({
      label: award.awardLabel,
      type: award.awardType,
      status: award.status,
      amountUsd: null,
    }),
  );
  const awards = summaryAwards.length > 0 ? summaryAwards : checklistAwards;
  if (awards.length < minimumTableRows) return [];
  const withAmounts = awards.some((award) => award.amountUsd !== null);
  return [
    table(
      undefined,
      [
        { key: "award", label: "Award" },
        { key: "type", label: "Type" },
        ...(withAmounts
          ? [{ key: "amount", label: "Amount", align: "right" as const }]
          : []),
        { key: "status", label: "Status" },
      ],
      awards.map((award) => ({
        award: award.label,
        type: titleCase(award.type),
        ...(withAmounts
          ? { amount: award.amountUsd === null ? "—" : formatUsd(award.amountUsd) }
          : {}),
        status: titleCase(award.status),
      })),
    ),
  ];
}

function accountBlocks(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
): AssistantBlock[] {
  if (requestType !== "student_account") return [];
  const charges = derived.account?.charges ?? [];
  if (charges.length < minimumTableRows) return [];
  return [
    table(
      undefined,
      [
        { key: "charge", label: "Charge" },
        { key: "amount", label: "Amount", align: "right" },
        { key: "status", label: "Status" },
        { key: "due", label: "Due" },
      ],
      charges.map((charge) => ({
        charge: charge.label,
        amount: formatUsd(charge.amountUsd),
        status: titleCase(charge.state),
        due: formatDate(charge.dueAt),
      })),
    ),
  ];
}

/** Blockers are reasons, not records. Bullets, per the brief. */
function blockerBlocks(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
): AssistantBlock[] {
  if (
    requestType !== "holds_and_blockers" &&
    requestType !== "registration_status" &&
    requestType !== "housing_eligibility"
  ) {
    return [];
  }
  const gates = [
    ...(derived.registration?.gates ?? []),
    ...(derived.housingEligibility?.gates ?? []),
  ].filter((gate) => !gate.satisfied);
  const holds = [...derived.officialHolds, ...derived.derivedBlockers];
  const items: AssistantListItem[] = [
    ...holds.flatMap((hold) =>
      hold.studentSafeReason
        ? [
            {
              text: hold.studentSafeReason,
              ...(hold.supportRoute ? { href: hold.supportRoute } : {}),
            },
          ]
        : [],
    ),
    ...gates.map((gate) => ({
      text: gate.reason,
      ...(gate.navigationRoute ? { href: gate.navigationRoute } : {}),
    })),
  ];
  if (items.length < minimumTableRows) return [];
  return [bulletList("What is standing in the way", items)];
}

/**
 * Dates tabulate well in bulk and badly one at a time. "When is orientation?"
 * asks for a single date; answering with the whole term calendar is a worse
 * answer than the sentence it displaced, so the question itself decides.
 */
const asksForManyDates =
  /\ball\b|\bevery\b|\bdeadlines\b|\bdates\b|\bcalendar\b|\bschedule\b|\bcoming up\b|\bupcoming\b|\bwhat(?:'s| is) (?:next|left)\b|\bmissed\b|\boverdue\b|\bimportant\b/i;

function deadlineBlocks(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
  classification: RequestClassification,
  question: string,
): AssistantBlock[] {
  if (requestType !== "deadlines" && requestType !== "academic_calendar") return [];
  if (
    classification.requestedEntity !== null ||
    classification.deadlineScope === "targeted" ||
    !asksForManyDates.test(question)
  ) {
    return [];
  }
  const deadlines = derived.deadlines;
  if (deadlines.length >= minimumTableRows) {
    return [
      table(
        undefined,
        [
          { key: "item", label: "Item" },
          { key: "due", label: "Due" },
          { key: "status", label: "Status" },
        ],
        deadlines.map((deadline) => ({
          item: deadline.title,
          due: formatDate(deadline.dueAt),
          status: urgencyLabel(deadline.urgency),
        })),
      ),
    ];
  }
  const events = derived.calendar?.events ?? [];
  if (requestType === "academic_calendar" && events.length >= minimumTableRows) {
    return [
      table(
        undefined,
        [
          { key: "event", label: "Event" },
          { key: "date", label: "Date" },
        ],
        events.map((event) => ({
          event: event.label,
          date: formatDate(event.startsAt),
        })),
      ),
    ];
  }
  return [];
}

/**
 * Ordered, because next steps are a sequence: the brief asks for a numbered
 * list when there is a process to follow. One action is a sentence, not a list.
 */
function nextStepBlocks(derived: DerivedStudentState): AssistantBlock[] {
  const items: AssistantNextStep[] = [];
  const action = derived.prioritizedAction;
  if (action) {
    items.push({
      text: action.label,
      owner: "student",
      ...(action.navigationRoute ? { href: action.navigationRoute } : {}),
    });
  }
  for (const state of derived.documentStates) {
    if (!isActionRequiredSubmissionState(state.submissionState)) continue;
    if (items.some((item) => item.text.includes(state.title))) continue;
    items.push({
      text: `${state.title} (${submissionStateLabel(state.submissionState).toLowerCase()})`,
      owner: "student",
      ...(state.navigationRoute ? { href: state.navigationRoute } : {}),
    });
  }
  return items.length >= minimumTableRows ? [nextSteps(items.slice(0, 6))] : [];
}

/**
 * A short answer-first sentence for the assembled path, replacing the
 * concatenated fact list that the detail blocks now carry properly.
 */
function leadSentence(
  requestType: StudentAssistantRequestType,
  derived: DerivedStudentState,
  fallback: string,
): string {
  if (requestType === "missing_documents" || requestType === "document_status") {
    const outstanding = derived.documentStates.filter((state) =>
      isActionRequiredSubmissionState(state.submissionState),
    ).length;
    const waiting = derived.documentStates.filter(
      (state) => state.submissionState === "UNDER_REVIEW",
    ).length;
    if (outstanding === 0 && waiting === 0) return fallback;
    const parts = [
      outstanding > 0
        ? `${outstanding} document${outstanding === 1 ? "" : "s"} still need${outstanding === 1 ? "s" : ""} your attention`
        : null,
      waiting > 0 ? `${waiting} ${waiting === 1 ? "is" : "are"} already with a reviewer` : null,
    ].filter(Boolean);
    return `${capitalise(parts.join(" and "))}.`;
  }
  if (requestType.startsWith("aid_")) {
    const awards = derived.financialAid?.awardAcceptanceStatuses ?? [];
    if (awards.length >= minimumTableRows) {
      const accepted = awards.filter((award) => award.status === "accepted").length;
      return `You have ${awards.length} financial aid award${awards.length === 1 ? "" : "s"} on file, ${accepted} of them accepted.`;
    }
  }
  if (requestType === "student_account") {
    const balance = derived.account?.balanceUsd;
    if (typeof balance === "number") {
      return `Your student account balance is ${formatUsd(balance)}.`;
    }
  }
  if (requestType === "deadlines" && derived.deadlines.length >= minimumTableRows) {
    return `You have ${derived.deadlines.length} deadlines on record.`;
  }
  return fallback;
}

function formatUsd(amount: number): string {
  return `$${amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "—";
  return new Date(parsed).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

function urgencyLabel(urgency: string): string {
  return {
    overdue: "Overdue",
    due_today: "Due today",
    due_within_7_days: "Due this week",
    upcoming: "Upcoming",
    unknown_date: "No date set",
  }[urgency] ?? titleCase(urgency);
}

function titleCase(value: string): string {
  return capitalise(value.replaceAll("_", " "));
}

function capitalise(value: string): string {
  return value.length === 0 ? value : value[0]!.toUpperCase() + value.slice(1);
}
