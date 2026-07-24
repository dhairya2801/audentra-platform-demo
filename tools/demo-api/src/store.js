import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createSeedState, FIXTURE_VERSION } from "./seed.js";

export const defaultDataFile = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../.data/state.json",
);

export class JsonStateStore {
  #state = null;
  #queue = Promise.resolve();

  constructor(filePath = defaultDataFile, clock = () => new Date()) {
    this.filePath = resolve(filePath);
    this.clock = clock;
  }

  async initialize() {
    await mkdir(dirname(this.filePath), { recursive: true });
    try {
      const parsed = JSON.parse(await readFile(this.filePath, "utf8"));
      validatePersistedState(parsed);
      this.#state = parsed;
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
    const state = createSeedState();
    await this.#write(state);
    this.#state = state;
    return this.snapshot();
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
      if (!shouldCommit) {
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

function validatePersistedState(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    value.schemaVersion !== 2 ||
    value.fixture?.version !== FIXTURE_VERSION
  ) {
    throw new Error(
      "The preview state file is incompatible. Run `npm run reset` in tools/demo-api.",
    );
  }
}
