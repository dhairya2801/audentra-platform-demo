import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { reconcileDocumentRequirements } from "./document-state.js";
import {
  createSeedState,
  FIXTURE_VERSION,
  TENANT_CONTENT_VERSION,
} from "./seed.js";
import { demoTenants, publicTenantContext } from "./tenant-config.js";
import { ensureManagedConfigurations } from "./managed-config.js";

export const defaultDataFile = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../.data/state.json",
);

export class JsonStateStore {
  #state = null;
  #queue = Promise.resolve();

  constructor(
    filePath = defaultDataFile,
    clock = () => new Date(),
    uploadDirectory,
    stateFactory = createSeedState,
  ) {
    this.filePath = resolve(filePath);
    this.clock = clock;
    this.uploadDirectory = resolve(
      uploadDirectory ??
        process.env.DOCUMENT_UPLOAD_DIR ??
        `${dirname(this.filePath)}/uploads`,
    );
    this.stateFactory = stateFactory;
  }

  async initialize() {
    await mkdir(dirname(this.filePath), { recursive: true });
    try {
      const parsed = migratePersistedState(
        JSON.parse(await readFile(this.filePath, "utf8")),
        this.stateFactory(),
      );
      validatePersistedState(parsed);
      // A state file written before the projection existed can carry the
      // divergence on disk, so it is repaired on load rather than inherited.
      reconcileDocumentRequirements(parsed);
      this.#state = parsed;
      await this.#write(parsed);
    } catch (error) {
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) {
        throw error;
      }
      await this.reset();
    }
    return this.snapshot();
  }

  snapshot() {
    if (!this.#state) {
      throw new Error("State store has not been initialized");
    }
    return structuredClone(this.#state);
  }

  async reset() {
    const state = this.stateFactory();
    reconcileDocumentRequirements(state);
    await this.#write(state);
    this.#state = state;
    return this.snapshot();
  }

  async writeUpload(storageKey, bytes) {
    validateStorageKey(storageKey);
    await mkdir(this.uploadDirectory, { recursive: true });
    const filePath = resolve(this.uploadDirectory, storageKey);
    assertInsideDirectory(filePath, this.uploadDirectory);
    const temporaryPath = `${filePath}.${randomUUID()}.uploading`;
    assertInsideDirectory(temporaryPath, this.uploadDirectory);
    await writeFile(temporaryPath, bytes, { mode: 0o600 });
    await rename(temporaryPath, filePath);
    return filePath;
  }

  async readUpload(storageKey) {
    validateStorageKey(storageKey);
    const filePath = resolve(this.uploadDirectory, storageKey);
    assertInsideDirectory(filePath, this.uploadDirectory);
    return readFile(filePath);
  }

  /**
   * Development-only provider journal. This is deliberately separate from the
   * student-facing document record: normalized extraction data belongs on the
   * document, while the exact provider response belongs to an attempt history.
   */
  async recordAiProviderResponse(response) {
    const record = {
      ...structuredClone(response),
      id: randomUUID(),
      recordedAt: response.recordedAt ?? this.clock().toISOString(),
    };
    return this.transact((draft) => {
      draft.aiProviderResponses ??= [];
      draft.aiProviderResponses.push(record);
      return record;
    });
  }

  async transact(mutator) {
    const operation = this.#queue.then(async () => {
      if (!this.#state) {
        throw new Error("State store has not been initialized");
      }
      const draft = structuredClone(this.#state);
      let shouldCommit = true;
      const result = await mutator(draft, {
        skipWrite() {
          shouldCommit = false;
        },
      });
      // Derived state is reconciled here, inside the transaction boundary,
      // rather than at each call site. A projection that a new mutation path
      // can forget to run is a staleness bug waiting to be written; this one
      // cannot be forgotten, because there is no way to commit around it.
      const reconciled = reconcileDocumentRequirements(draft);
      if (!shouldCommit && reconciled === 0) {
        return structuredClone(result);
      }
      draft.fixture.revision += 1;
      draft.fixture.updatedAt = this.clock().toISOString();
      await this.#write(draft);
      this.#state = draft;
      return structuredClone(result);
    });
    this.#queue = operation.catch(() => undefined);
    return operation;
  }

  async #write(state) {
    await mkdir(dirname(this.filePath), { recursive: true });
    const temporaryFile = `${this.filePath}.${process.pid}.tmp`;
    await writeFile(temporaryFile, `${JSON.stringify(state, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    await rename(temporaryFile, this.filePath);
  }
}

function migratePersistedState(value, seededState) {
  if (value && typeof value === "object" && !value.tenant) {
    value.tenant = publicTenantContext(demoTenants.aster);
  }
  if (!value?.onboarding || typeof value.onboarding !== "object") {
    return value;
  }
  if (value.onboarding.currentStep === "other_records") {
    value.onboarding.currentStep = "family_permissions";
  }
  if (Array.isArray(value.onboarding.completedSteps)) {
    value.onboarding.completedSteps =
      value.onboarding.completedSteps.filter(
        (step) => step !== "other_records",
      );
  }
  const data = value.onboarding.data;
  if (data && typeof data === "object" && !Array.isArray(data)) {
    if (Array.isArray(data.skippedSteps)) {
      data.skippedSteps = data.skippedSteps.filter(
        (step) => step !== "other_records",
      );
    }
    for (const legacyField of [
      "legalNameConfirmed",
      "contactInformationConfirmed",
      "homeAddressConfirmed",
      "emergencyContactConfirmed",
      "recordsConfirmed",
      "familyPermissionsReviewed",
      "signatureConfirmed",
      "depositAcknowledged",
    ]) {
      delete data[legacyField];
    }
  }
  if (value.fixture?.contentVersion !== TENANT_CONTENT_VERSION) {
    value.academicCatalog = structuredClone(seededState.academicCatalog);
    value.campusLife = structuredClone(seededState.campusLife);
    value.academics ??= {};
    value.academics.selectedProgramCode =
      seededState.academics.selectedProgramCode;
    value.academics.exemptionRecommendations ??= [];
    value.rewards = {
      ...structuredClone(seededState.rewards),
      ledger: Array.isArray(value.rewards?.ledger)
        ? value.rewards.ledger
        : [],
    };
    value.fixture.contentVersion = TENANT_CONTENT_VERSION;
  }
  if (
    !value.rewards?.program ||
    !Array.isArray(value.rewards?.rules) ||
    !Array.isArray(value.rewards?.ledger)
  ) {
    value.rewards = {
      ...structuredClone(seededState.rewards),
      ledger: Array.isArray(value.rewards?.ledger)
        ? value.rewards.ledger
        : [],
    };
  }
  if (
    !value.staff ||
    !Array.isArray(value.staff.members) ||
    !Array.isArray(value.staff.workItems) ||
    !Array.isArray(value.staff.workLogs)
  ) {
    value.staff = structuredClone(seededState.staff);
  }
  value.staff.knowledgeBase = Array.isArray(value.staff.knowledgeBase)
    ? value.staff.knowledgeBase
    : structuredClone(seededState.staff.knowledgeBase);
  value.staff.corePlays = Array.isArray(value.staff.corePlays)
    ? value.staff.corePlays
    : structuredClone(seededState.staff.corePlays);
  value.staff.journeyBlueprint = Array.isArray(value.staff.journeyBlueprint)
    ? value.staff.journeyBlueprint
    : structuredClone(seededState.staff.journeyBlueprint);
  value.staff.outreachRuns = Array.isArray(value.staff.outreachRuns)
    ? value.staff.outreachRuns
    : [];
  ensureManagedConfigurations(value, seededState);
  value.staff.cohort = Array.isArray(value.staff.cohort)
    ? value.staff.cohort
    : structuredClone(seededState.staff.cohort);
  value.staff.cohortSeed =
    value.staff.cohortSeed ??
    structuredClone(seededState.staff.cohortSeed);
  const existingWorkItemIds = new Set(
    value.staff.workItems.map((item) => item.id),
  );
  for (const item of seededState.staff.workItems) {
    if (!existingWorkItemIds.has(item.id)) {
      value.staff.workItems.push(structuredClone(item));
    }
  }
  const existingWorkLogIds = new Set(
    value.staff.workLogs.map((item) => item.id),
  );
  for (const log of seededState.staff.workLogs) {
    if (!existingWorkLogIds.has(log.id)) {
      value.staff.workLogs.push(structuredClone(log));
    }
  }
  value.helpRequests =
    Array.isArray(value.helpRequests) && value.helpRequests.length > 0
      ? value.helpRequests
      : structuredClone(seededState.helpRequests);
  for (const inquiry of value.helpRequests) {
    inquiry.subject ??=
      `Student question about ${String(inquiry.topicCode).replaceAll("_", " ")}`;
    inquiry.status = inquiry.status === "received" ? "new" : inquiry.status;
    inquiry.priority ??= "medium";
    inquiry.assigneeId ??= null;
    inquiry.updatedAt ??= inquiry.createdAt;
    inquiry.version ??= 1;
  }
  for (const [index, club] of (value.campusLife?.clubs ?? []).entries()) {
    club.version ??= 1;
    club.updatedAt ??=
      seededState.campusLife.clubs[index]?.updatedAt ??
      seededState.fixture.updatedAt;
  }
  return value;
}

function validateStorageKey(storageKey) {
  if (
    typeof storageKey !== "string" ||
    !/^[0-9a-f-]{36}\.[a-z0-9]{2,5}$/i.test(storageKey)
  ) {
    throw new Error("Invalid document storage key");
  }
}

function assertInsideDirectory(filePath, directory) {
  const relativePath = relative(directory, filePath);
  if (
    relativePath.length === 0 ||
    isAbsolute(relativePath) ||
    relativePath === ".." ||
    relativePath.startsWith(`..\\`) ||
    relativePath.startsWith("../")
  ) {
    throw new Error("Document storage path escaped its upload directory");
  }
}

function validatePersistedState(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    value.schemaVersion !== 4 ||
    value.fixture?.version !== FIXTURE_VERSION
  ) {
    throw new Error(
      "The preview state file is incompatible. Run `npm run reset` in tools/demo-api.",
    );
  }
}
