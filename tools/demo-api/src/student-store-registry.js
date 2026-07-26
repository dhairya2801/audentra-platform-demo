import { mkdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createSeedState } from "./seed.js";
import { JsonStateStore } from "./store.js";

export class StudentStoreRegistry {
  #stores = new Map();

  constructor(directory, clock = () => new Date()) {
    this.directory = resolve(directory);
    this.clock = clock;
  }

  async initialize() {
    await mkdir(this.directory, { recursive: true });
  }

  async get(account) {
    const existing = this.#stores.get(account.id);
    if (existing) return existing;
    const store = new JsonStateStore(
      join(this.directory, `${account.studentId}.json`),
      this.clock,
      join(this.directory, "uploads", account.studentId),
      () =>
        createSeedState({
          actorId: account.actorId,
          studentId: account.studentId,
          email: account.email,
          phone: account.phone,
          freshStudent: true,
        }),
    );
    await store.initialize();
    this.#stores.set(account.id, store);
    return store;
  }

  cachedStores() {
    return [...this.#stores.values()];
  }

  async recordAiProviderResponse(response, accounts, fallbackStore) {
    for (const account of accounts) {
      const store = await this.get(account);
      if (
        store
          .snapshot()
          .documents.some((document) => document.id === response.documentId)
      ) {
        return store.recordAiProviderResponse(response);
      }
    }
    return fallbackStore.recordAiProviderResponse(response);
  }
}
