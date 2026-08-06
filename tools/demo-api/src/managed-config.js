import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { load, dump } from "js-yaml";
import {
  badRequest,
  conflict,
  notFound,
} from "./errors.js";

const configurationKinds = Object.freeze([
  "journeys",
  "campus_life",
  "academics",
]);

const journeyTaskTypes = Object.freeze([
  "information",
  "form",
  "upload_file",
  "approval",
  "single_select",
  "multiple_select",
  "selection_flow",
  "signature",
  "payment",
  "scheduling",
]);

const submissionTypes = Object.freeze([
  "none",
  "form",
  "document",
  "payment",
  "appointment",
]);

const eventCategories = Object.freeze([
  "academic",
  "social",
  "career",
  "wellness",
  "athletics",
]);

const eventAccents = Object.freeze(["gold", "navy", "blue", "coral"]);
const eventThemes = Object.freeze([
  "festival",
  "discovery",
  "career",
  "community",
]);

const canonicalRequirementIds = Object.freeze({
  profile_verification: "00000000-0000-7000-8000-000000000601",
  identity_document: "00000000-0000-7000-8000-000000000602",
  enrollment_deposit: "00000000-0000-7000-8000-000000000603",
  official_transcript: "00000000-0000-7000-8000-000000000604",
  financial_aid_verification: "00000000-0000-7000-8000-000000000605",
  immunization_record: "00000000-0000-7000-8000-000000000606",
  housing_preference: "00000000-0000-7000-8000-000000000607",
  orientation_registration: "00000000-0000-7000-8000-000000000608",
});

const asterJourneySeed = readFileSync(
  new URL("../../../config/tenants/aster/journeys.yaml", import.meta.url),
  "utf8",
);

function configurationFileName(tenantSlug, kind) {
  const name = kind === "campus_life" ? "campus-life" : kind;
  return `config/tenants/${tenantSlug}/${name}.yaml`;
}

function yamlText(value) {
  return dump(value, {
    noRefs: true,
    lineWidth: 100,
    noCompatMode: true,
    sortKeys: false,
    quotingType: '"',
  });
}

function tenantAuthoredSource(tenantName) {
  return {
    label: `${tenantName} staff configuration`,
    url: "#",
    dataStatus: "tenant_authored",
  };
}

export function createManagedConfigurations(state, tenant, updatedAt) {
  const journeyDocument =
    tenant.slug === "aster"
      ? parseYamlDocument("journeys", asterJourneySeed)
      : journeyDocumentFromState(state, tenant.slug);
  const campusDocument = campusDocumentFromState(state, tenant.slug);
  const academicDocument = academicDocumentFromState(state, tenant.slug);

  const configurations = {
    journeys: managedConfiguration(
      "journeys",
      tenant.slug,
      journeyDocument,
      updatedAt,
    ),
    campus_life: managedConfiguration(
      "campus_life",
      tenant.slug,
      campusDocument,
      updatedAt,
    ),
    academics: managedConfiguration(
      "academics",
      tenant.slug,
      academicDocument,
      updatedAt,
    ),
  };

  projectConfiguration(state, "journeys", journeyDocument, {
    version: 1,
    tenantName: tenant.name,
  });
  projectConfiguration(state, "campus_life", campusDocument, {
    version: 1,
    tenantName: tenant.name,
  });
  projectConfiguration(state, "academics", academicDocument, {
    version: 1,
    tenantName: tenant.name,
  });
  return configurations;
}

function managedConfiguration(kind, tenantSlug, document, updatedAt) {
  return {
    kind,
    fileName: configurationFileName(tenantSlug, kind),
    version: 1,
    yaml: yamlText(document),
    updatedAt,
    updatedBy: "Seed configuration",
  };
}

export function ensureManagedConfigurations(state, seededState) {
  state.staff.managedConfigurations ??= structuredClone(
    seededState.staff.managedConfigurations,
  );
  for (const kind of configurationKinds) {
    if (!state.staff.managedConfigurations[kind]) {
      state.staff.managedConfigurations[kind] = structuredClone(
        seededState.staff.managedConfigurations[kind],
      );
    }
    const configuration = state.staff.managedConfigurations[kind];
    const document = parseYamlDocument(kind, configuration.yaml);
    projectConfiguration(state, kind, document, {
      version: configuration.version,
      tenantName: state.tenant.name,
    });
  }
}

export function getManagedConfiguration(state, kind) {
  requireConfigurationKind(kind);
  const configuration = state.staff.managedConfigurations?.[kind];
  if (!configuration) {
    throw notFound(
      "STAFF_CONFIGURATION_NOT_FOUND",
      "The managed configuration was not found",
    );
  }
  return structuredClone(configuration);
}

export function updateManagedConfiguration(
  draft,
  kind,
  input,
  actorName,
  now,
) {
  requireConfigurationKind(kind);
  const current = draft.staff.managedConfigurations?.[kind];
  if (!current) {
    throw notFound(
      "STAFF_CONFIGURATION_NOT_FOUND",
      "The managed configuration was not found",
    );
  }
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw badRequest(
      "INVALID_CONFIGURATION_UPDATE",
      "The configuration update must be an object",
    );
  }
  const expectedVersion = Number(input.expectedVersion);
  if (!Number.isInteger(expectedVersion) || expectedVersion < 1) {
    throw badRequest(
      "INVALID_CONFIGURATION_VERSION",
      "expectedVersion must be a positive integer",
    );
  }
  if (current.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This configuration changed in another staff session",
    );
  }
  if (typeof input.yaml !== "string" || input.yaml.trim().length < 20) {
    throw badRequest(
      "INVALID_CONFIGURATION_YAML",
      "Provide a non-empty YAML configuration",
    );
  }
  if (input.yaml.length > 200_000) {
    throw badRequest(
      "CONFIGURATION_TOO_LARGE",
      "The YAML configuration is too large",
    );
  }

  const document = parseYamlDocument(kind, input.yaml);
  const version = current.version + 1;
  projectConfiguration(draft, kind, document, {
    version,
    tenantName: draft.tenant.name,
  });
  current.version = version;
  current.yaml = yamlText(document);
  current.updatedAt = now.toISOString();
  current.updatedBy = actorName;
  current.changeSummary =
    typeof input.changeSummary === "string" && input.changeSummary.trim()
      ? input.changeSummary.trim().slice(0, 240)
      : "Published a validated YAML configuration.";
  return structuredClone(current);
}

export function draftManagedConfigurationWithEdward(
  state,
  kind,
  input,
) {
  requireConfigurationKind(kind);
  const current = getManagedConfiguration(state, kind);
  const instruction =
    typeof input?.instruction === "string" ? input.instruction.trim() : "";
  if (instruction.length < 5 || instruction.length > 2_000) {
    throw badRequest(
      "INVALID_EDWARD_INSTRUCTION",
      "Describe the requested change in 5 to 2,000 characters",
    );
  }
  if (
    Number(input.expectedVersion) !== current.version
  ) {
    throw conflict(
      "VERSION_CONFLICT",
      "The configuration changed before Edward prepared the draft",
    );
  }

  const document = parseYamlDocument(kind, current.yaml);
  const result =
    kind === "journeys"
      ? draftJourneyChange(document, instruction)
      : kind === "campus_life"
        ? draftCampusChange(document, instruction)
        : draftAcademicChange(document, instruction);

  return {
    kind,
    expectedVersion: current.version,
    yaml: yamlText(result.document),
    summary: result.summary,
    changes: result.changes,
    warnings: result.warnings,
    executionMode: "draft_requires_confirmation",
  };
}

export function enrollmentRequirementsFromConfiguration(
  state,
  acceptedAt,
) {
  const configuration = getManagedConfiguration(state, "journeys");
  const document = parseYamlDocument("journeys", configuration.yaml);
  const flow = document.flows.find((candidate) => candidate.kind === "enrollment");
  if (!flow) return [];
  const accepted = new Date(acceptedAt);
  return flow.tasks.map((task, index) => {
    const dependencies = Array.isArray(task.depends_on)
      ? task.depends_on.map(String)
      : [];
    const dueDays = Number.isInteger(task.due_days_after_acceptance)
      ? task.due_days_after_acceptance
      : 14 + index * 3;
    const submissionType =
      task.submission_type ??
      submissionTypeForTaskType(task.task_type);
    return {
      id:
        canonicalRequirementIds[task.id] ??
        stableUuid(`requirement:${flow.id}:${task.id}`),
      code: task.id,
      title: task.title,
      description: task.description,
      status: dependencies.length ? "blocked" : "ready",
      blocking: task.required,
      priority: Number.isInteger(task.priority) ? task.priority : 0,
      order: index + 1,
      dueAt: new Date(
        accepted.getTime() + dueDays * 86_400_000,
      ).toISOString(),
      progressPercent: task.initial_progress_percent ?? 0,
      dependsOnCodes: dependencies,
      submissionType,
      flowKind: flow.kind,
      interactionType: task.task_type,
      inputConfig: {
        ...(Array.isArray(task.options) ? { options: [...task.options] } : {}),
        ...(Number.isInteger(task.maximum_selections)
          ? { maximumSelections: task.maximum_selections }
          : {}),
        ...(Array.isArray(task.fields)
          ? { fields: structuredClone(task.fields) }
          : {}),
        ...(Array.isArray(task.flow) ? { flow: structuredClone(task.flow) } : {}),
        ...(typeof task.signature_provider === "string"
          ? { signatureProvider: task.signature_provider }
          : {}),
        ...(typeof task.docusign_template_id === "string"
          ? { docusignTemplateId: task.docusign_template_id }
          : {}),
        ...(Array.isArray(task.accepted_mime_types)
          ? { acceptedMimeTypes: [...task.accepted_mime_types] }
          : {}),
        ...(Array.isArray(task.document_categories)
          ? { documentCategories: [...task.document_categories] }
          : {}),
      },
      responsibleOffice: task.owner,
      configurationVersion: configuration.version,
    };
  });
}

export function managedConfigurationSummary(configuration) {
  const document = parseYamlDocument(
    configuration.kind,
    configuration.yaml,
  );
  const recordCount =
    configuration.kind === "journeys"
      ? document.flows.reduce((total, flow) => total + flow.tasks.length, 0)
      : configuration.kind === "campus_life"
        ? document.events.length
        : document.courses.length;
  return {
    ...structuredClone(configuration),
    recordCount,
  };
}

function parseYamlDocument(kind, yaml) {
  let document;
  try {
    document = typeof yaml === "string" ? load(yaml) : yaml;
  } catch (error) {
    throw badRequest(
      "INVALID_CONFIGURATION_YAML",
      `The YAML could not be parsed: ${error instanceof Error ? error.message : "invalid syntax"}`,
    );
  }
  if (!isRecord(document)) {
    throw badRequest(
      "INVALID_CONFIGURATION_YAML",
      "The YAML root must be an object",
    );
  }
  if (document.schema_version !== 1) {
    throw badRequest(
      "UNSUPPORTED_CONFIGURATION_SCHEMA",
      "schema_version must be 1",
    );
  }
  if (document.configuration !== kind) {
    throw badRequest(
      "CONFIGURATION_KIND_MISMATCH",
      `configuration must be ${kind}`,
    );
  }
  if (kind === "journeys") validateJourneyDocument(document);
  if (kind === "campus_life") validateCampusDocument(document);
  if (kind === "academics") validateAcademicDocument(document);
  return document;
}

function validateJourneyDocument(document) {
  requireText(document.tenant, "tenant");
  requireArray(document.flows, "flows", { min: 1, max: 20 });
  const taskIds = new Set();
  for (const [flowIndex, flow] of document.flows.entries()) {
    const path = `flows[${flowIndex}]`;
    if (!isRecord(flow)) invalid(`${path} must be an object`);
    requireIdentifier(flow.id, `${path}.id`);
    requireText(flow.title, `${path}.title`);
    requireEnum(flow.kind, `${path}.kind`, ["onboarding", "enrollment"]);
    requireEnum(flow.status, `${path}.status`, [
      "draft",
      "published",
      "archived",
    ]);
    requireArray(flow.tasks, `${path}.tasks`, { min: 1, max: 100 });
    for (const [taskIndex, task] of flow.tasks.entries()) {
      const taskPath = `${path}.tasks[${taskIndex}]`;
      if (!isRecord(task)) invalid(`${taskPath} must be an object`);
      requireIdentifier(task.id, `${taskPath}.id`);
      if (taskIds.has(task.id)) {
        invalid(`Task id ${task.id} is duplicated`);
      }
      taskIds.add(task.id);
      requireText(task.title, `${taskPath}.title`);
      requireText(task.description, `${taskPath}.description`);
      requireEnum(task.task_type, `${taskPath}.task_type`, journeyTaskTypes);
      requireText(task.owner, `${taskPath}.owner`);
      if (typeof task.required !== "boolean") {
        invalid(`${taskPath}.required must be true or false`);
      }
      requireInteger(task.points, `${taskPath}.points`, 0, 10_000);
      if (task.priority !== undefined) {
        requireInteger(task.priority, `${taskPath}.priority`, 0, 100);
      }
      if (task.due_days_after_acceptance !== undefined) {
        requireInteger(
          task.due_days_after_acceptance,
          `${taskPath}.due_days_after_acceptance`,
          0,
          3_650,
        );
      }
      if (task.submission_type !== undefined) {
        requireEnum(
          task.submission_type,
          `${taskPath}.submission_type`,
          submissionTypes,
        );
      }
      if (task.initial_progress_percent !== undefined) {
        requireInteger(
          task.initial_progress_percent,
          `${taskPath}.initial_progress_percent`,
          0,
          100,
        );
      }
      if (task.depends_on !== undefined) {
        requireArray(task.depends_on, `${taskPath}.depends_on`, {
          min: 0,
          max: 20,
        });
        for (const dependency of task.depends_on) {
          requireIdentifier(dependency, `${taskPath}.depends_on`);
        }
      }
      if (task.flow !== undefined) {
        requireArray(task.flow, `${taskPath}.flow`, { min: 1, max: 30 });
        for (const [stepIndex, step] of task.flow.entries()) {
          const stepPath = `${taskPath}.flow[${stepIndex}]`;
          if (!isRecord(step)) invalid(`${stepPath} must be an object`);
          requireIdentifier(step.id, `${stepPath}.id`);
          requireText(step.title, `${stepPath}.title`);
          requireText(step.field_type, `${stepPath}.field_type`);
          if (typeof step.required !== "boolean") {
            invalid(`${stepPath}.required must be true or false`);
          }
        }
      }
    }
  }
}

function validateCampusDocument(document) {
  requireText(document.tenant, "tenant");
  requireArray(document.events, "events", { min: 0, max: 500 });
  const ids = new Set();
  for (const [index, event] of document.events.entries()) {
    const path = `events[${index}]`;
    if (!isRecord(event)) invalid(`${path} must be an object`);
    requireIdentifier(event.id, `${path}.id`);
    if (ids.has(event.id)) invalid(`Event id ${event.id} is duplicated`);
    ids.add(event.id);
    requireText(event.title, `${path}.title`);
    requireText(event.description, `${path}.description`);
    requireIsoDate(event.starts_at, `${path}.starts_at`);
    requireIsoDate(event.ends_at, `${path}.ends_at`);
    if (Date.parse(event.ends_at) <= Date.parse(event.starts_at)) {
      invalid(`${path}.ends_at must be after starts_at`);
    }
    requireText(event.location, `${path}.location`);
    requireEnum(event.category, `${path}.category`, eventCategories);
    if (typeof event.featured !== "boolean") {
      invalid(`${path}.featured must be true or false`);
    }
    requireEnum(event.accent, `${path}.accent`, eventAccents);
    requireEnum(event.visual_theme, `${path}.visual_theme`, eventThemes);
  }
}

function validateAcademicDocument(document) {
  requireText(document.tenant, "tenant");
  requireText(document.catalog_version, "catalog_version");
  requireArray(document.courses, "courses", { min: 1, max: 5_000 });
  const codes = new Set();
  for (const [index, course] of document.courses.entries()) {
    const path = `courses[${index}]`;
    if (!isRecord(course)) invalid(`${path} must be an object`);
    requireIdentifier(course.id, `${path}.id`);
    requireText(course.code, `${path}.code`);
    if (codes.has(course.code)) invalid(`Course code ${course.code} is duplicated`);
    codes.add(course.code);
    requireText(course.title, `${path}.title`);
    requireText(course.description, `${path}.description`);
    requireInteger(course.credits, `${path}.credits`, 0, 30);
    requireInteger(course.level, `${path}.level`, 0, 9_999);
    requireArray(course.prerequisites, `${path}.prerequisites`, {
      min: 0,
      max: 30,
    });
  }
}

function projectConfiguration(state, kind, document, options) {
  if (kind === "journeys") {
    state.staff.journeyBlueprint = document.flows.flatMap((flow) =>
      flow.tasks.map((task, order) => ({
        id: task.id,
        kind: flow.kind,
        flowId: flow.id,
        flowTitle: flow.title,
        title: task.title,
        description: task.description,
        owner: task.owner,
        required: task.required,
        published: flow.status === "published",
        priority: Number.isInteger(task.priority) ? task.priority : 0,
        order: order + 1,
        dueOffsetDays: Number.isInteger(task.due_days_after_acceptance)
          ? task.due_days_after_acceptance
          : null,
        taskType: task.task_type,
        submissionType:
          task.submission_type ?? submissionTypeForTaskType(task.task_type),
        points: task.points,
        studentStep: task.student_step ?? null,
        dependsOn: Array.isArray(task.depends_on)
          ? task.depends_on.map(String)
          : [],
        flow: Array.isArray(task.flow) ? structuredClone(task.flow) : [],
        configurationVersion: options.version,
      })),
    );
    syncJourneyRewardRules(state, document);
    return;
  }
  if (kind === "campus_life") {
    const existingById = new Map(
      (state.campusLife.events ?? []).map((event) => [event.id, event]),
    );
    state.campusLife.events = document.events.map((event) => {
      const existing = existingById.get(event.id);
      return {
        id: event.id,
        title: event.title,
        description: event.description,
        startsAt: event.starts_at,
        endsAt: event.ends_at,
        location: event.location,
        category: event.category,
        featured: event.featured,
        accent: event.accent,
        visualTheme: event.visual_theme,
        imageUrl: event.image_url ?? existing?.imageUrl ?? null,
        imageAlt: event.image_alt ?? existing?.imageAlt ?? null,
        imageAttribution:
          event.image_attribution ?? existing?.imageAttribution ?? null,
        imageSourceUrl:
          event.image_source_url ?? existing?.imageSourceUrl ?? null,
        source: existing?.source ?? tenantAuthoredSource(options.tenantName),
        registrationUrl: event.registration_url ?? null,
        version: options.version,
      };
    });
    return;
  }

  const existingByCode = new Map(
    (state.academicCatalog.courses ?? []).map((course) => [course.code, course]),
  );
  state.academicCatalog.version = document.catalog_version;
  state.academicCatalog.courses = document.courses.map((course) => {
    const existing = existingByCode.get(course.code);
    return {
      id: course.id.includes("-") && course.id.length === 36
        ? course.id
        : stableUuid(`course:${course.id}`),
      code: course.code,
      title: course.title,
      description: course.description,
      credits: course.credits,
      level: course.level,
      availabilityLabel: course.availability_label ?? null,
      instructorNames: Array.isArray(course.instructor_names)
        ? course.instructor_names.map(String)
        : [],
      meetingPattern: course.meeting_pattern ?? null,
      prerequisites: course.prerequisites.map((prerequisite) => ({
        courseCode: prerequisite.course_code,
        minimumGrade: prerequisite.minimum_grade ?? null,
      })),
      source: existing?.source ?? tenantAuthoredSource(options.tenantName),
      resources: existing?.resources ?? [],
      configurationVersion: options.version,
    };
  });
}

function journeyDocumentFromState(state, tenantSlug) {
  return {
    schema_version: 1,
    tenant: tenantSlug,
    configuration: "journeys",
    flows: ["onboarding", "enrollment"].map((kind) => ({
      id: kind === "onboarding" ? "offer_onboarding" : "enrollment_checklist",
      title: kind === "onboarding" ? "Offer onboarding" : "Enrollment checklist",
      kind,
      status: "published",
      tasks: state.staff.journeyBlueprint
        .filter((item) => item.kind === kind)
        .map((item) => ({
          id: item.id,
          title: item.title,
          description: item.description,
          task_type: item.taskType ?? "form",
          submission_type: item.submissionType ?? "form",
          owner: item.owner,
          required: item.required,
          points: item.points ?? 20,
        })),
    })),
  };
}

function campusDocumentFromState(state, tenantSlug) {
  return {
    schema_version: 1,
    tenant: tenantSlug,
    configuration: "campus_life",
    events: state.campusLife.events.map((event) => ({
      id: event.id,
      title: event.title,
      description: event.description,
      starts_at: event.startsAt,
      ends_at: event.endsAt,
      location: event.location,
      category: event.category,
      featured: event.featured,
      accent: event.accent,
      visual_theme: event.visualTheme ?? "community",
      image_url: event.imageUrl ?? null,
      image_alt: event.imageAlt ?? null,
      image_attribution: event.imageAttribution ?? null,
      image_source_url: event.imageSourceUrl ?? null,
      registration_url: event.registrationUrl ?? null,
    })),
  };
}

function academicDocumentFromState(state, tenantSlug) {
  return {
    schema_version: 1,
    tenant: tenantSlug,
    configuration: "academics",
    catalog_version: state.academicCatalog.version,
    courses: state.academicCatalog.courses.map((course) => ({
      id: course.id,
      code: course.code,
      title: course.title,
      description: course.description,
      credits: course.credits,
      level: course.level,
      prerequisites: course.prerequisites.map((prerequisite) => ({
        course_code: prerequisite.courseCode,
        minimum_grade: prerequisite.minimumGrade,
      })),
      instructor_names: course.instructorNames ?? [],
      meeting_pattern: course.meetingPattern ?? null,
      availability_label: course.availabilityLabel ?? null,
    })),
  };
}

function syncJourneyRewardRules(state, document) {
  if (!state.rewards?.rules) return;
  const tasks = document.flows.flatMap((flow) => flow.tasks);
  const configuredCodes = new Set(tasks.map((task) => task.id));
  const retained = state.rewards.rules.filter(
    (rule) =>
      rule.triggerType !== "requirement_completed" ||
      !configuredCodes.has(rule.triggerKey),
  );
  const nextRules = tasks
    .filter((task) => task.points > 0)
    .map((task, index) => ({
      id: `reward-rule:${state.tenant.slug}:complete_${task.id}`,
      code: `complete_${task.id}`,
      title: task.title,
      description: `Complete ${task.title.toLowerCase()}.`,
      triggerType: "requirement_completed",
      triggerKey: task.id,
      triggerProperties: {},
      points: task.points,
      maxAwardsPerStudent: 1,
      displayOrder: 100 + index,
      enabled: true,
    }));
  state.rewards.rules = [...retained, ...nextRules];
}

function draftJourneyChange(document, instruction) {
  const lower = instruction.toLowerCase();
  const pointChange = instruction.match(
    /(?:make|set|change|update)\s+(?:the\s+)?(?:points?\s+(?:for|of)\s+)?["“]?(.+?)["”]?\s+(?:worth|to|at)\s+(\d+)\s+(?:aster\s+|harvard\s+|college\s+)?points?/i,
  );
  if (pointChange) {
    const title = pointChange[1].trim();
    const flowCandidates = lower.includes("onboarding")
      ? document.flows.filter((candidate) => candidate.kind === "onboarding")
      : lower.includes("enrollment")
        ? document.flows.filter((candidate) => candidate.kind === "enrollment")
        : document.flows;
    const match = flowCandidates
      .flatMap((candidate) =>
        candidate.tasks.map((task) => ({ flow: candidate, task })),
      )
      .find(({ task }) =>
        task.title.toLowerCase().includes(title.toLowerCase()),
      );
    if (!match) {
      return noDeterministicDraft(
        document,
        `I could not find a task matching "${title}".`,
      );
    }
    match.task.points = Number(pointChange[2]);
    return {
      document,
      summary: `Set ${match.task.title} to ${match.task.points} points.`,
      changes: [
        `Updated points for ${match.task.id} in ${match.flow.title}.`,
      ],
      warnings: [],
    };
  }
  const flow = document.flows.find((candidate) =>
    lower.includes("onboarding")
      ? candidate.kind === "onboarding"
      : candidate.kind === "enrollment",
  );
  if (!flow) {
    return noDeterministicDraft(
      document,
      "Specify whether the change belongs to offer onboarding or the enrollment checklist.",
    );
  }
  if (!/\badd\b/i.test(instruction)) {
    return noDeterministicDraft(
      document,
      "This local Edward adapter currently supports adding tasks or changing task points. You can still edit the YAML directly.",
    );
  }

  const quoted = instruction.match(/["“]([^"”]+)["”]/)?.[1];
  const rawTitle =
    quoted ??
    instruction
      .replace(/^.*?\badd\b/i, "")
      .split(/\b(?:to|with|worth|for)\b/i)[0]
      .trim();
  const title = sentenceCase(rawTitle || "New enrollment task");
  const taskType = inferTaskType(lower);
  const points = Number(instruction.match(/(\d+)\s+(?:\w+\s+)?points?/i)?.[1] ?? 20);
  const id = uniqueIdentifier(
    slugify(title),
    new Set(flow.tasks.map((task) => task.id)),
  );
  const task = {
    id,
    title,
    description: `Complete ${title.toLowerCase()} as part of ${flow.title.toLowerCase()}.`,
    task_type: taskType,
    submission_type: submissionTypeForTaskType(taskType),
    owner: inferOwner(lower),
    required: !/\boptional\b/i.test(instruction),
    points,
  };
  if (taskType === "selection_flow") {
    task.flow = [
      {
        id: "preference",
        title: "Choose a preference",
        field_type: "single_select",
        required: true,
        options: ["option_one", "option_two", "undecided"],
      },
      {
        id: "details",
        title: "Add optional details",
        field_type: "conditional_form",
        required: false,
        when: { field: "preference", equals: "option_one" },
      },
    ];
  }
  flow.tasks.push(task);
  return {
    document,
    summary: `Added ${title} to ${flow.title}.`,
    changes: [
      `Added task ${id}.`,
      `Set task type to ${taskType}.`,
      `Set completion reward to ${points} points.`,
    ],
    warnings:
      taskType === "selection_flow"
        ? ["Review the generated option values and conditional logic before publishing."]
        : [],
  };
}

function draftCampusChange(document, instruction) {
  if (!/\badd\b/i.test(instruction) || !/\bevent\b/i.test(instruction)) {
    return noDeterministicDraft(
      document,
      "This local Edward adapter currently supports adding campus events. You can still edit the YAML directly.",
    );
  }
  const title =
    instruction.match(/["“]([^"”]+)["”]/)?.[1] ?? "New campus event";
  const start =
    instruction.match(/\b(20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:\.\d{3})?Z)?)\b/)?.[1] ??
    "2027-09-15T18:00:00.000Z";
  const startsAt = start.includes("T") ? start : `${start}T18:00:00.000Z`;
  const endsAt = new Date(Date.parse(startsAt) + 2 * 3_600_000).toISOString();
  const location =
    instruction.match(/\bat\s+([^,.]+)(?:[,.]|$)/i)?.[1]?.trim() ??
    "Campus location to confirm";
  const id = uniqueIdentifier(
    slugify(title),
    new Set(document.events.map((event) => event.id)),
  );
  document.events.push({
    id,
    title,
    description: `Join the ${title} campus experience.`,
    starts_at: startsAt,
    ends_at: endsAt,
    location,
    category: inferEventCategory(instruction.toLowerCase()),
    featured: true,
    accent: "gold",
    visual_theme: "community",
    registration_url: null,
  });
  return {
    document,
    summary: `Added ${title} to upcoming campus events.`,
    changes: [`Added event ${id}.`, "Featured the event in the student carousel."],
    warnings: [
      "Review the generated description, time, location, and registration URL before publishing.",
    ],
  };
}

function draftAcademicChange(document, instruction) {
  if (!/\badd\b/i.test(instruction) || !/\bcourse\b/i.test(instruction)) {
    return noDeterministicDraft(
      document,
      "This local Edward adapter currently supports adding courses. You can still edit the YAML directly.",
    );
  }
  const code = instruction.match(/\b([A-Z]{2,5}\s*\d{3}[A-Z]?)\b/)?.[1]
    ?.replace(/\s+/, " ");
  if (!code) {
    return noDeterministicDraft(
      document,
      "Include a course code such as CS 250.",
    );
  }
  if (document.courses.some((course) => course.code === code)) {
    return noDeterministicDraft(document, `${code} already exists.`);
  }
  const title =
    instruction.match(/["“]([^"”]+)["”]/)?.[1] ?? `${code} course`;
  const credits = Number(instruction.match(/(\d+)\s+credits?/i)?.[1] ?? 3);
  document.courses.push({
    id: slugify(code),
    code,
    title,
    description: `Tenant-authored course description for ${title}.`,
    credits,
    level: Number(code.match(/\d{3}/)?.[0] ?? 100),
    prerequisites: [],
    instructor_names: [],
    meeting_pattern: null,
    availability_label: "Schedule to be confirmed",
  });
  return {
    document,
    summary: `Added ${code}: ${title}.`,
    changes: [`Added ${credits}-credit course ${code}.`],
    warnings: [
      "Review prerequisites, official catalog wording, instructor assignments, and meeting pattern before publishing.",
    ],
  };
}

function noDeterministicDraft(document, warning) {
  return {
    document,
    summary: "No YAML changes were made.",
    changes: [],
    warnings: [warning],
  };
}

function submissionTypeForTaskType(taskType) {
  if (taskType === "upload_file") return "document";
  if (taskType === "payment") return "payment";
  if (taskType === "scheduling") return "appointment";
  if (taskType === "information") return "none";
  return "form";
}

function inferTaskType(lower) {
  if (/\bupload|file|photo|document\b/.test(lower)) return "upload_file";
  if (/\bpay|payment|deposit\b/.test(lower)) return "payment";
  if (/\bsign|signature\b/.test(lower)) return "signature";
  if (/\bapprove|accept|review\b/.test(lower)) return "approval";
  if (/\broommate|flow|rank|sequence\b/.test(lower)) return "selection_flow";
  if (/\bmultiple|several|interests\b/.test(lower)) return "multiple_select";
  if (/\bselect|choose|option\b/.test(lower)) return "single_select";
  if (/\bschedule|appointment|session\b/.test(lower)) return "scheduling";
  return "form";
}

function inferOwner(lower) {
  if (lower.includes("housing")) return "Housing";
  if (lower.includes("financial") || lower.includes("aid")) return "Financial Aid";
  if (lower.includes("course") || lower.includes("transcript")) return "Registrar";
  if (lower.includes("campus") || lower.includes("club")) return "Student Life";
  return "Admissions";
}

function inferEventCategory(lower) {
  if (lower.includes("career")) return "career";
  if (lower.includes("wellness") || lower.includes("health")) return "wellness";
  if (lower.includes("athletic") || lower.includes("sport")) return "athletics";
  if (lower.includes("academic") || lower.includes("research")) return "academic";
  return "social";
}

function stableUuid(seed) {
  const value = createHash("sha256").update(seed).digest("hex");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-7${value.slice(13, 16)}-8${value.slice(17, 20)}-${value.slice(20, 32)}`;
}

function requireConfigurationKind(kind) {
  if (!configurationKinds.includes(kind)) {
    throw notFound(
      "STAFF_CONFIGURATION_NOT_FOUND",
      "The managed configuration was not found",
    );
  }
}

function requireArray(value, path, { min, max }) {
  if (!Array.isArray(value) || value.length < min || value.length > max) {
    invalid(`${path} must contain between ${min} and ${max} items`);
  }
}

function requireText(value, path) {
  if (typeof value !== "string" || !value.trim() || value.length > 2_000) {
    invalid(`${path} must be a non-empty string`);
  }
}

function requireIdentifier(value, path) {
  if (
    typeof value !== "string" ||
    !/^[a-z0-9][a-z0-9_-]{1,79}$/i.test(value)
  ) {
    invalid(`${path} must be a stable identifier`);
  }
}

function requireInteger(value, path, min, max) {
  if (!Number.isInteger(value) || value < min || value > max) {
    invalid(`${path} must be an integer between ${min} and ${max}`);
  }
}

function requireEnum(value, path, values) {
  if (!values.includes(value)) {
    invalid(`${path} must be one of: ${values.join(", ")}`);
  }
}

function requireIsoDate(value, path) {
  if (
    typeof value !== "string" ||
    !Number.isFinite(Date.parse(value)) ||
    !value.includes("T")
  ) {
    invalid(`${path} must be an ISO-8601 date-time`);
  }
}

function invalid(message) {
  throw badRequest("INVALID_CONFIGURATION_SCHEMA", message);
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function slugify(value) {
  return String(value)
    .normalize("NFKD")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64) || "managed_item";
}

function uniqueIdentifier(base, ids) {
  let candidate = base;
  let suffix = 2;
  while (ids.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function sentenceCase(value) {
  const text = value.trim();
  return text ? `${text.slice(0, 1).toUpperCase()}${text.slice(1)}` : text;
}
