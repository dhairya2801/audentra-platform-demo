import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { CORE_SCHEMA, load } from "js-yaml";
import {
  getManagedConfiguration,
  updateManagedConfiguration,
} from "../src/managed-config.js";
import { createSeedState } from "../src/seed.js";

const updatedAt = new Date("2026-08-23T12:00:00.000Z");

const campusLifeDocument = `schema_version: 1
tenant: aster
configuration: campus_life
events:
  - id: yaml_upgrade_event
    title: yes
    description: "true"
    starts_at: 2027-09-18T18:00:00.000Z
    ends_at: 2027-09-18T20:00:00.000Z
    location: "false"
    category: social
    featured: true
    accent: gold
    visual_theme: community
`;

describe("managed configuration YAML", () => {
  it("serializes and reloads YAML 1.2 core scalars with double quotes", () => {
    const state = createSeedState();
    const current = getManagedConfiguration(state, "campus_life");

    const updated = updateManagedConfiguration(
      state,
      "campus_life",
      {
        expectedVersion: current.version,
        yaml: campusLifeDocument,
      },
      "Test staff",
      updatedAt,
    );

    assert.match(updated.yaml, /title: yes\r?\n/);
    assert.match(updated.yaml, /description: "true"\r?\n/);
    assert.match(updated.yaml, /location: "false"\r?\n/);

    const parsed = load(updated.yaml, { schema: CORE_SCHEMA });
    assert.equal(parsed.events[0].title, "yes");
    assert.equal(parsed.events[0].description, "true");
    assert.equal(parsed.events[0].location, "false");

    const roundTripped = updateManagedConfiguration(
      state,
      "campus_life",
      {
        expectedVersion: updated.version,
        yaml: updated.yaml,
      },
      "Test staff",
      updatedAt,
    );
    assert.deepEqual(
      load(roundTripped.yaml, { schema: CORE_SCHEMA }),
      parsed,
    );
  });

  it("rejects an empty YAML document with the existing root-validation error", () => {
    const state = createSeedState();
    const current = getManagedConfiguration(state, "campus_life");

    assert.throws(
      () =>
        updateManagedConfiguration(
          state,
          "campus_life",
          {
            expectedVersion: current.version,
            yaml: "# This YAML document has no content\n# beyond comments\n",
          },
          "Test staff",
          updatedAt,
        ),
      (error) => {
        assert.equal(error.code, "INVALID_CONFIGURATION_YAML");
        assert.equal(error.message, "The YAML root must be an object");
        return true;
      },
    );
  });
});
