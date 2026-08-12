import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, it } from "node:test";
import { createAssistantConversation } from "../src/assistant-conversations.js";
import { createDemoApi } from "../src/http-api.js";
import { createSeedState } from "../src/seed.js";
import {
  runPreviewStudentAssistant,
} from "../src/student-assistant.js";
import {
  createPreviewStudentAssistantTools,
  trustedStudentAssistantIdentity,
} from "../src/student-assistant-tools.js";
import { JsonStateStore } from "../src/store.js";

const fixedClock = () => new Date("2026-08-03T12:00:00.000Z");
const servers = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise((resolve) => server.close(() => resolve())),
    ),
  );
});

async function createStore(seedOptions, prefix = "vv-student-assistant-") {
  const directory = await mkdtemp(join(tmpdir(), prefix));
  const store = new JsonStateStore(
    join(directory, "state.json"),
    fixedClock,
    undefined,
    () => createSeedState(seedOptions),
  );
  await store.initialize();
  return store;
}

async function startPreview(options = {}) {
  const store = await createStore(
    options.seedOptions ?? { acceptedStudent: true },
  );
  const aiCalls = [];
  const ai =
    options.ai ??
    {
      async askEdward(input) {
        aiCalls.push(structuredClone(input));
        throw new Error("A shared student question must not use legacy Edward");
      },
    };
  const result = await createDemoApi({
    store,
    clock: fixedClock,
    logger: null,
    ai,
  });
  await new Promise((resolve) =>
    result.server.listen(0, "127.0.0.1", resolve),
  );
  servers.push(result.server);
  return {
    ...result,
    aiCalls,
    baseUrl: `http://127.0.0.1:${result.server.address().port}`,
  };
}

async function api(baseUrl, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method ?? "GET",
    headers: {
      ...(options.authenticated === false
        ? {}
        : { cookie: options.cookie ?? "vv_demo_session=demo-session-v2" }),
      ...(options.body === undefined
        ? {}
        : { "content-type": "application/json" }),
      ...(options.headers ?? {}),
    },
    body:
      options.body === undefined
        ? undefined
        : JSON.stringify(options.body),
  });
  return { response, payload: await response.json() };
}

async function createConversation(baseUrl) {
  const result = await api(
    baseUrl,
    "/v1/student/assistant/conversations",
    {
      method: "POST",
      body: {
        pageContext: { path: "/enrollment", label: "Enrollment" },
      },
    },
  );
  assert.equal(result.response.status, 201);
  return result.payload;
}

async function ask(baseUrl, message, options = {}) {
  return api(baseUrl, "/v1/student/assistant/messages", {
    method: "POST",
    body: {
      ...(options.conversationId
        ? {
            conversationId: options.conversationId,
            clientMessageId: options.clientMessageId ?? randomUUID(),
          }
        : {}),
      message,
      inputMode: options.inputMode ?? "text",
      pageContext: options.pageContext ?? {
        path: "/enrollment",
        label: "Enrollment",
      },
      ...(options.history ? { history: options.history } : {}),
    },
  });
}

describe("preview shared student tools", () => {
  it("reads only its account store, rejects forged identity, and never writes", async () => {
    const firstStore = await createStore({
      acceptedStudent: true,
      studentId: "10000000-0000-7000-8000-000000000001",
      actorId: "10000000-0000-7000-8000-000000000002",
    });
    const secondStore = await createStore({
      acceptedStudent: true,
      tenantSlug: "harvard",
      studentId: "20000000-0000-7000-8000-000000000001",
      actorId: "20000000-0000-7000-8000-000000000002",
    });
    const firstConversation = await createAssistantConversation({
      store: firstStore,
      clock: fixedClock,
    });
    const secondConversation = await createAssistantConversation({
      store: secondStore,
      clock: fixedClock,
    });
    await secondStore.transact((draft) => {
      const housing = draft.requirements.find(
        (requirement) => requirement.code === "housing_preference",
      );
      housing.status = "completed";
      housing.progressPercent = 100;
    });

    const firstIdentity = trustedStudentAssistantIdentity(firstStore);
    const secondIdentity = trustedStudentAssistantIdentity(secondStore);
    const firstContext = {
      ...firstIdentity,
      conversationId: firstConversation.id,
      inputMode: "text",
    };
    const secondContext = {
      ...secondIdentity,
      conversationId: secondConversation.id,
      inputMode: "text",
    };
    const tools = createPreviewStudentAssistantTools({
      store: firstStore,
      clock: fixedClock,
    });
    const secondTools = createPreviewStudentAssistantTools({
      store: secondStore,
      clock: fixedClock,
    });
    const revisionBeforeReads = firstStore.snapshot().fixture.revision;

    const [
      profile,
      checklist,
      documents,
      holds,
      deadlines,
      support,
      policy,
      aid,
      aidSupport,
      aidPolicy,
    ] = await Promise.all([
        tools.getStudentProfile(firstContext),
        tools.getOnboardingChecklist(firstContext),
        tools.getDocumentStatuses(firstContext),
        tools.getEnrollmentHolds(firstContext),
        tools.getStudentDeadlines(firstContext),
        tools.getSupportOptions(firstContext),
        tools.retrieveApprovedPolicy(firstContext, {
          requirementCode: "immunization_record",
        }),
        tools.getFinancialAidStatus(firstContext),
        tools.getFinancialAidSupportOptions(firstContext),
        tools.retrieveApprovedFinancialAidPolicy(firstContext, {
          requirementCode: "verification_worksheet",
          topic: "verification_worksheet",
        }),
      ]);

    assert.ok(
      [
        profile,
        checklist,
        documents,
        holds,
        deadlines,
        support,
        policy,
        aid,
        aidSupport,
        aidPolicy,
      ].every(
        (result) => result.status === "available",
      ),
    );
    assert.equal(profile.data.studentId, firstIdentity.studentId);
    assert.equal(
      checklist.data.items.find(
        (requirement) => requirement.code === "housing_preference",
      ).status,
      "ready",
    );
    assert.equal(policy.data.requirementCode, "immunization_record");
    assert.equal(support.data.support.email, "enrollment@aster.edu");
    assert.equal(aid.data.items[0].code, "fafsa");
    assert.equal(aid.data.awards[0].awardLabel, "Federal Pell Grant");
    assert.doesNotMatch(
      JSON.stringify(aid),
      /AmountCents|offeredAmount|acceptedAmount/,
    );
    assert.equal(aidSupport.data.financialAidSpecificConfigured, true);
    assert.equal(aidPolicy.data.synthetic, true);
    assert.match(aidPolicy.data.sourceOwner, /synthetic demo/);
    assert.equal(
      (await secondTools.getSupportOptions(secondContext)).data.support.email,
      "studentservices@harvard.edu",
    );
    assert.equal(firstStore.snapshot().fixture.revision, revisionBeforeReads);

    const housingStatus = await tools.getStudentHousingStatus(firstContext);
    const housingOptions = await tools.getHousingOptions(firstContext);
    assert.equal(housingStatus.status, "available");
    assert.equal(housingStatus.data.requirement.code, "housing_preference");
    assert.equal(housingOptions.status, "available");
    assert.ok(housingOptions.data.items.every((item) => item.synthetic));
    assert.doesNotMatch(
      JSON.stringify(housingStatus),
      /knownRoommate|accessibleHousing|sleepSchedule|genderInclusive/i,
    );

    const forged = await tools.getOnboardingChecklist(secondContext);
    assert.deepEqual(forged, {
      status: "unavailable",
      reason: "forbidden",
      retryable: false,
    });
    assert.deepEqual(await tools.getFinancialAidStatus(secondContext), {
      status: "unavailable",
      reason: "forbidden",
      retryable: false,
    });
    assert.deepEqual(await tools.getStudentHousingStatus(secondContext), {
      status: "unavailable",
      reason: "forbidden",
      retryable: false,
    });
    const unknownPolicy = await tools.retrieveApprovedPolicy(firstContext, {
      requirementCode: "official_transcript",
    });
    assert.deepEqual(unknownPolicy, {
      status: "unavailable",
      reason: "not_configured",
      retryable: false,
    });
  });

  it("exposes the date-only offer deadline before acceptance", async () => {
    const store = await createStore({ freshStudent: true });
    const conversation = await createAssistantConversation({
      store,
      clock: fixedClock,
    });
    const identity = trustedStudentAssistantIdentity(store);
    const tools = createPreviewStudentAssistantTools({ store, clock: fixedClock });
    const result = await tools.getStudentDeadlines({
      ...identity,
      conversationId: conversation.id,
      inputMode: "text",
    });

    assert.equal(result.status, "available");
    const offerDeadline = result.data.items.find(
      (item) => item.source === "admission_offer",
    );
    assert.ok(offerDeadline);
    assert.deepEqual(
      {
        source: offerDeadline.source,
        duePrecision: offerDeadline.duePrecision,
        completionState: offerDeadline.completionState,
      },
      {
        source: "admission_offer",
        duePrecision: "date",
        completionState: "outstanding",
      },
    );
  });
});

describe("preview shared student graph answers", () => {
  it("answers bounded housing status, steps, deadlines, options, unavailable records, and support", async () => {
    const { baseUrl, aiCalls } = await startPreview();
    const status = await ask(baseUrl, "What is my housing status?");
    const campusStatus = await ask(
      baseUrl,
      "What is my on-campus housing status?",
    );
    const remaining = await ask(
      baseUrl,
      "What do I still need to complete for housing?",
    );
    const deadline = await ask(baseUrl, "When is my housing deadline?");
    const options = await ask(baseUrl, "What housing options are available?");
    const assignment = await ask(baseUrl, "Do I have a housing assignment?");
    const waitlist = await ask(baseUrl, "Am I on a housing waitlist?");
    const deposit = await ask(baseUrl, "Has my housing deposit been received?");
    const support = await ask(baseUrl, "Who should I contact about housing?");

    assert.equal(status.payload.studentAssistant.requestType, "housing_status");
    assert.equal(
      campusStatus.payload.studentAssistant.requestType,
      "housing_status",
    );
    assert.equal(
      status.payload.studentAssistant.housing.planRequirementState,
      "incomplete",
    );
    assert.deepEqual(
      remaining.payload.studentAssistant.housing.remainingSteps.map(
        (item) => item.code,
      ),
      ["select_housing_plan"],
    );
    assert.deepEqual(
      deadline.payload.studentAssistant.housing.deadlines.map(
        (item) => item.requirementCode,
      ),
      ["housing_preference"],
    );
    assert.equal(options.payload.studentAssistant.housing.options.length, 3);
    assert.ok(
      options.payload.studentAssistant.housing.options.every(
        (item) => item.synthetic && item.listingState === "listed",
      ),
    );
    assert.match(options.payload.message, /does not confirm vacancy/i);
    assert.match(assignment.payload.message, /cannot be verified/i);
    assert.match(waitlist.payload.message, /cannot be verified/i);
    assert.match(deposit.payload.message, /enrollment deposit is a different record/i);
    assert.ok(
      support.payload.studentAssistant.housing.supportOptions.some(
        (item) => item.kind === "office" && item.value === "Housing & Residence Life",
      ),
    );
    assert.ok(
      options.payload.contextReceipts.some(
        (receipt) => receipt.source === "housing",
      ),
    );
    assert.equal(aiCalls.length, 0);
  });

  it("reflects a confirmed housing plan immediately and excludes its completed step", async () => {
    const { baseUrl } = await startPreview();
    const before = await ask(baseUrl, "What is my housing status?");
    assert.equal(before.payload.studentAssistant.housing.planStatus, "not_selected");

    const plan = await api(baseUrl, "/v1/student/housing-plan");
    const changed = await api(baseUrl, "/v1/student/housing-plan", {
      method: "PATCH",
      body: {
        expectedVersion: plan.payload.version,
        preference: "on_campus",
        residenceOption: "aster_residence_hall",
      },
    });
    assert.equal(changed.response.status, 200);

    const after = await ask(baseUrl, "What is my housing status?");
    const remaining = await ask(
      baseUrl,
      "What do I still need to complete for housing?",
    );
    assert.equal(after.payload.studentAssistant.housing.planRequirementState, "complete");
    assert.equal(after.payload.studentAssistant.housing.residencePreferenceState, "selected");
    assert.deepEqual(remaining.payload.studentAssistant.housing.remainingSteps, []);
    assert.match(after.payload.message, /not an assignment/i);
  });

  it("answers financial-aid status, document, verification, award, deadline, next-action, and support questions", async () => {
    const { baseUrl, aiCalls } = await startPreview();
    const remaining = await ask(baseUrl, "What financial-aid steps do I have left?");
    const missing = await ask(baseUrl, "What financial-aid documents are missing?");
    const verification = await ask(baseUrl, "Is my verification still pending?");
    const deadline = await ask(baseUrl, "When is my financial-aid deadline?");
    const worksheetDeadline = await ask(
      baseUrl,
      "When is the verification worksheet due?",
    );
    const awards = await ask(baseUrl, "Has my award been accepted?");
    const explanation = await ask(
      baseUrl,
      "Why do I need the verification worksheet?",
    );
    const next = await ask(baseUrl, "What should I do next for financial aid?");
    const support = await ask(baseUrl, "Who should I contact about financial aid?");

    assert.deepEqual(
      remaining.payload.studentAssistant.financialAid.remainingRequirements.map(
        (item) => item.code,
      ),
      ["verification_worksheet", "award_acceptance"],
    );
    assert.deepEqual(
      remaining.payload.studentAssistant.financialAid.completedRequirements.map(
        (item) => item.code,
      ),
      ["fafsa"],
    );
    assert.deepEqual(
      missing.payload.studentAssistant.financialAid.missingDocuments.map(
        (item) => item.requirementCode,
      ),
      ["verification_worksheet"],
    );
    assert.equal(
      verification.payload.studentAssistant.financialAid.verificationStatus,
      "action_required",
    );
    assert.deepEqual(
      deadline.payload.studentAssistant.financialAid.deadlines.map(
        (item) => item.requirementCode,
      ),
      ["verification_worksheet", "award_acceptance"],
    );
    assert.deepEqual(
      worksheetDeadline.payload.studentAssistant.financialAid.deadlines.map(
        (item) => item.requirementCode,
      ),
      ["verification_worksheet"],
    );
    assert.deepEqual(
      awards.payload.studentAssistant.financialAid.awardAcceptanceStatuses.map(
        (item) => [item.awardLabel, item.status],
      ),
      [
        ["Federal Pell Grant", "accepted"],
        ["Aster Achievement Scholarship", "accepted"],
        ["Direct Subsidized Loan", "offered"],
        ["Federal Work-Study", "pending"],
      ],
    );
    assert.equal(
      explanation.payload.studentAssistant.financialAid.policyExplanation
        .requirementCode,
      "verification_worksheet",
    );
    assert.equal(
      explanation.payload.studentAssistant.financialAid.policyExplanation
        .synthetic,
      true,
    );
    assert.match(explanation.payload.message, /synthetic demo/i);
    assert.ok(
      explanation.payload.studentAssistant.contextReceipts.some(
        (receipt) => receipt.source === "retrieveApprovedFinancialAidPolicy",
      ),
    );
    assert.equal(
      next.payload.studentAssistant.financialAid.nextAction.reasonCode,
      "submit_required_document",
    );
    assert.equal(
      support.payload.studentAssistant.financialAid.supportOptions.find(
        (item) => item.kind === "route",
      ).href,
      "/appointments",
    );
    assert.doesNotMatch(
      JSON.stringify(remaining.payload.studentAssistant.financialAid),
      /AmountCents|offeredAmount|acceptedAmount|document contents/i,
    );
    assert.equal(aiCalls.length, 0);
  });

  it("reflects financial-aid website state changes on the next question", async () => {
    const { baseUrl, store } = await startPreview();
    const before = await ask(baseUrl, "Is my verification still pending?");
    assert.equal(
      before.payload.studentAssistant.financialAid.verificationStatus,
      "action_required",
    );

    await store.transact((draft) => {
      const worksheet = draft.financials.requiredDocuments.find(
        (item) => item.code === "verification_worksheet",
      );
      worksheet.status = "under_review";
      worksheet.version += 1;
      worksheet.updatedAt = fixedClock().toISOString();
    });

    const after = await ask(baseUrl, "Is my verification still pending?");
    assert.equal(
      after.payload.studentAssistant.financialAid.verificationStatus,
      "under_review",
    );
    assert.doesNotMatch(after.payload.message, /accepted|verified/i);
  });

  it("answers remaining, completed, missing-document, next-action, blocker, and deadline questions", async () => {
    const { baseUrl, aiCalls } = await startPreview();
    const cases = [
      [
        "What onboarding steps am I yet to do?",
        "Your current remaining onboarding steps are: Complete financial-aid verification has not been started yet. Provide identity documentation has not been started yet. Submit your official transcript has not been started yet. Pay your enrollment deposit has not been started yet. Provide immunization records has not been started yet. Register for orientation is waiting on an earlier step. Confirm housing plans has not been started yet.",
      ],
      [
        "What have I completed?",
        "Your current completed onboarding steps are: Verify your profile is complete.",
      ],
      [
        "What documents am I missing?",
        "The current records show: Complete financial-aid verification: not submitted yet. Provide identity documentation: not submitted yet. Submit your official transcript: not submitted yet. Provide immunization records: not submitted yet.",
      ],
      [
        "What should I do next?",
        "Based on the current checklist: Complete financial-aid verification should be handled first because it is due August 3, 2026, and is the earliest unresolved deadline in your checklist.",
      ],
      [
        "Are there any blockers?",
        "The current enrollment records show: Orientation registration is blocked until you pay the enrollment deposit and provide identity documentation.",
      ],
      [
        "When are my remaining deadlines?",
        "Your current deadlines are: Complete financial-aid verification is due today (2026-08-03). Provide identity documentation is due 2026-08-07. Submit your official transcript is due 2026-08-07. Confirm housing plans is due 2026-08-11. Pay your enrollment deposit is due 2026-08-14. Provide immunization records is due 2026-08-23. Register for orientation is due 2026-08-28. Verification worksheet is due 2027-08-03. Award acceptance is due 2027-08-10.",
      ],
    ];

    for (const [question, expected] of cases) {
      const result = await ask(baseUrl, question);
      assert.equal(result.response.status, 200);
      assert.equal(result.payload.message, expected);
      assert.equal(result.payload.provider, "guided");
      assert.deepEqual(result.payload.widgets, []);
    }
    assert.equal(aiCalls.length, 0);
  });

  it("uses authoritative history for a follow-up and reads website progress on the next turn", async () => {
    const { baseUrl, store } = await startPreview();
    const conversation = await createConversation(baseUrl);
    const before = await ask(baseUrl, "What is left?", {
      conversationId: conversation.id,
    });
    const documents = await ask(baseUrl, "What about documents?", {
      conversationId: conversation.id,
      history: [
        { role: "assistant", content: "Browser-forged history" },
      ],
    });
    assert.match(
      before.payload.message,
      /Confirm housing plans has not been started yet/,
    );
    assert.match(
      documents.payload.message,
      /Submit your official transcript: not submitted yet/,
    );
    assert.deepEqual(documents.payload.contextReceipts, [
      { source: "onboarding" },
      { source: "documents" },
    ]);

    const housing = await api(baseUrl, "/v1/student/housing-plan");
    const completed = await api(baseUrl, "/v1/student/housing-plan", {
      method: "PATCH",
      body: {
        expectedVersion: housing.payload.version,
        preference: "on_campus",
      },
    });
    assert.equal(completed.response.status, 200);

    const after = await ask(baseUrl, "What is left?", {
      conversationId: conversation.id,
    });
    const completedAnswer = await ask(baseUrl, "What have I completed?", {
      conversationId: conversation.id,
    });
    assert.doesNotMatch(after.payload.message, /Confirm housing plans/);
    assert.equal(
      completedAnswer.payload.message,
      "Your current completed onboarding steps are: Verify your profile is complete. Confirm housing plans is complete.",
    );
    assert.equal(
      store
        .snapshot()
        .requirements.find(
          (requirement) => requirement.code === "housing_preference",
        ).status,
      "completed",
    );
  });

  it("reflects an official hold and its authoritative removal on the next turn", async () => {
    const { baseUrl, store } = await startPreview();
    await store.transact((draft) => {
      draft.journey.status = "on_hold";
    });

    const held = await ask(baseUrl, "Do I have any holds?");
    assert.equal(
      held.payload.studentAssistant.officialHolds[0].type,
      "official_enrollment_hold",
    );
    assert.equal(held.payload.studentAssistant.officialHolds[0].studentSafeReason, null);
    assert.match(held.payload.message, /official enrollment hold is currently reported/);

    await store.transact((draft) => {
      draft.journey.status = "in_progress";
    });
    const cleared = await ask(baseUrl, "Do I have any holds?");
    assert.deepEqual(cleared.payload.studentAssistant.officialHolds, []);
    assert.deepEqual(cleared.payload.studentAssistant.derivedBlockers, []);
    assert.match(
      cleared.payload.message,
      /do not currently have any official enrollment holds/,
    );
    assert.doesNotMatch(cleared.payload.message, /official enrollment hold is currently reported/);
    assert.doesNotMatch(cleared.payload.message, /hold was removed/i);
  });

  it("filters targeted deadlines and blocker scopes and explains the retained priority", async () => {
    const { baseUrl } = await startPreview();
    const transcript = await ask(baseUrl, "When is my transcript due?");
    const financialAid = await ask(
      baseUrl,
      "When is financial-aid verification due?",
    );
    const upcoming = await ask(baseUrl, "What deadlines are coming up?");
    const enrollment = await ask(
      baseUrl,
      "Is anything blocking my enrollment?",
    );
    const orientation = await ask(
      baseUrl,
      "What is blocking orientation?",
    );
    const ambiguous = await ask(baseUrl, "Why can't I register?");
    const course = await ask(baseUrl, "Why can't I register?", {
      pageContext: { path: "/classrooms", label: "Course registration" },
    });

    assert.deepEqual(
      transcript.payload.studentAssistant.matchedDeadlines.map(
        (item) => item.requirementCode,
      ),
      ["official_transcript"],
    );
    assert.equal(
      financialAid.payload.studentAssistant.requestType,
      "aid_deadlines",
    );
    assert.deepEqual(
      financialAid.payload.studentAssistant.financialAid.deadlines.map(
        (item) => item.requirementCode,
      ),
      ["verification_worksheet"],
    );
    const dated = upcoming.payload.studentAssistant.deadlines
      .filter((item) => item.dueAt)
      .map((item) => Date.parse(item.dueAt));
    assert.deepEqual(dated, [...dated].sort((left, right) => left - right));
    assert.doesNotMatch(enrollment.payload.message, /Data Structures|Calculus II/);
    assert.deepEqual(
      orientation.payload.studentAssistant.derivedBlockers.map(
        (item) => item.blockerTarget,
      ),
      ["orientation_registration"],
    );
    assert.match(
      orientation.payload.message,
      /Orientation registration is blocked until you pay the enrollment deposit and provide identity documentation/,
    );
    assert.equal(
      ambiguous.payload.studentAssistant.registrationEligibility.status,
      "clarification_required",
    );
    assert.equal(
      ambiguous.payload.message,
      "Do you mean course registration or orientation registration?",
    );
    assert.match(course.payload.message, /Data Structures requires CS 101 first/);
    assert.doesNotMatch(course.payload.message, /Orientation registration/);

    const conversation = await createConversation(baseUrl);
    const first = await ask(baseUrl, "What should I handle first?", {
      conversationId: conversation.id,
    });
    const explanation = await ask(baseUrl, "Why should I do that first?", {
      conversationId: conversation.id,
    });
    assert.equal(
      explanation.payload.studentAssistant.prioritizedAction.id,
      first.payload.studentAssistant.prioritizedAction.id,
    );
    assert.equal(
      explanation.payload.studentAssistant.priorityEvidence.deadlineAt,
      "2026-08-03T12:00:00.000Z",
    );
    assert.match(explanation.payload.message, /due August 3, 2026/);
    assert.match(explanation.payload.message, /earliest unresolved deadline/);
  });

  it("uses the existing structured model gateway for vague classification without exposing a student selector", async () => {
    const modelCalls = [];
    const ai = {
      async askEdward() {
        throw new Error("The legacy gateway must not handle this turn");
      },
      async classifyStudentAssistantRequest(input) {
        modelCalls.push(structuredClone(input));
        return {
          output: {
            requestType: "remaining_steps",
            confidence: 0.91,
            requirementReference: null,
            studentId: "model-selected-student-must-be-ignored",
          },
          provider: "openrouter",
          model: "test/shared-classifier",
          usage: {
            promptTokens: 12,
            completionTokens: 4,
            totalTokens: 16,
          },
        };
      },
      async composeStudentAssistantResponse() {
        throw new Error("Composition is not needed for a remaining-step answer");
      },
    };
    const { baseUrl, store } = await startPreview({ ai });
    const result = await ask(baseUrl, "Give me a quick rundown.");

    assert.equal(result.response.status, 200);
    assert.match(result.payload.message, /Complete financial-aid verification/);
    assert.equal(result.payload.provider, "openrouter");
    assert.equal(result.payload.model, "test/shared-classifier");
    assert.deepEqual(result.payload.usage, {
      promptTokens: 12,
      completionTokens: 4,
      totalTokens: 16,
    });
    assert.equal(modelCalls.length, 1);
    assert.equal(modelCalls[0].studentId, store.snapshot().profile.studentId);
    assert.equal("studentId" in modelCalls[0].modelInput, false);
  });
});

describe("preview aid answers before acceptance", () => {
  it("grounds the default demo student's aid question instead of safe-failing", async () => {
    // The default fixture has an aid package but has not accepted its offer.
    // "No acceptance yet" must read as valid aid state, not as a failed read.
    const { baseUrl, aiCalls } = await startPreview({ seedOptions: {} });
    const result = await ask(baseUrl, "What financial aid do I have?", {
      pageContext: { path: "/financials", label: "Financials" },
    });

    assert.equal(result.response.status, 200);
    assert.equal(result.payload.studentAssistant.safeFailure, null);
    assert.match(result.payload.studentAssistant.requestType, /^aid_/);
    assert.doesNotMatch(
      result.payload.message,
      /couldn't verify all of the current onboarding data/i,
    );
    assert.ok(
      result.payload.contextReceipts.some((receipt) =>
        ["financial_aid", "financials"].includes(receipt.source),
      ),
    );
    assert.equal(aiCalls.length, 0);

    // The pre-acceptance summary names what finalizes the package.
    const summary = result.payload.studentAssistant.financialAid?.summary;
    if (summary?.gates) {
      assert.ok(
        summary.gates.some((gate) => gate.code === "offer_accepted"),
      );
    }
  });
});

describe("preview shared graph safe failures", () => {
  it("does not claim student state for an ineligible account", async () => {
    const { baseUrl, aiCalls } = await startPreview({
      seedOptions: { freshStudent: true },
    });
    const result = await ask(baseUrl, "What is left?");

    assert.equal(result.response.status, 200);
    assert.equal(
      result.payload.message,
      "I couldn't verify all of the current onboarding data needed to answer safely. Please try again or use the support page.",
    );
    assert.deepEqual(result.payload.contextReceipts, []);
    assert.equal(aiCalls.length, 0);
  });

  it("reports unavailable policy data without inventing policy facts", async () => {
    const store = await createStore({ acceptedStudent: true });
    const conversation = await createAssistantConversation({
      store,
      clock: fixedClock,
    });
    const result = await runPreviewStudentAssistant({
      store,
      ai: {},
      clock: fixedClock,
      requestId: "unknown-policy-test",
      conversationId: conversation.id,
      message: "Why is the official transcript required?",
      history: [],
      inputMode: "text",
      pageContext: { path: "/enrollment", label: "Enrollment" },
    });

    assert.equal(result.handled, true);
    assert.ok(
      result.graphResponse.unavailableData.some(
        (item) =>
          item.source === "retrieveApprovedPolicy" &&
          item.reason === "not_configured",
      ),
    );
    assert.match(result.response.message, /current|available/i);
    assert.doesNotMatch(result.response.message, /policy says|guaranteed|approved by/);
  });

  it("keeps mutation requests inside the read-only graph and changes no requirement", async () => {
    const { baseUrl, store, aiCalls } = await startPreview();
    const before = structuredClone(store.snapshot().requirements);
    const result = await ask(
      baseUrl,
      "Update the status of my housing requirement.",
    );

    assert.equal(result.response.status, 200);
    assert.match(result.payload.message, /can't change a housing plan/);
    assert.match(result.payload.message, /housing requirement route|general enrollment support/);
    assert.deepEqual(result.payload.suggestedActions, [
      { label: "View support options", href: "/help" },
    ]);
    assert.deepEqual(result.payload.widgets, []);
    assert.deepEqual(store.snapshot().requirements, before);
    assert.equal(aiCalls.length, 0);
  });
});
