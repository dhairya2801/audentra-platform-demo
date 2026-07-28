import { dirname, join } from "node:path";
import { createSeedState } from "./seed.js";
import { JsonStateStore } from "./store.js";
import { demoTenants, tenantConfigForSlug } from "./tenant-config.js";

export class TenantStoreRegistry {
  #stores = new Map();

  constructor(primaryStore, clock = () => new Date()) {
    this.primaryStore = primaryStore;
    this.clock = clock;
  }

  async initialize() {
    this.#stores.set(demoTenants.aster.slug, this.primaryStore);
    const directory = dirname(this.primaryStore.filePath);
    const harvardStore = new JsonStateStore(
      join(directory, "tenants", demoTenants.harvard.slug, "state.json"),
      this.clock,
      join(directory, "tenants", demoTenants.harvard.slug, "uploads"),
      () => createSeedState({ tenantSlug: demoTenants.harvard.slug }),
    );
    await harvardStore.initialize();
    this.#stores.set(demoTenants.harvard.slug, harvardStore);
  }

  get(slug) {
    const tenant = tenantConfigForSlug(slug);
    return tenant ? this.#stores.get(tenant.slug) ?? null : null;
  }

  entries() {
    return [...this.#stores.entries()];
  }
}
