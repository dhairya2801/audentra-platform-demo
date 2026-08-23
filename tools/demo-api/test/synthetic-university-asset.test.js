/**
 * The packaged archive must still be the universe the generator produces.
 *
 * The Python seeder reads a checked-in `.json.gz` rather than running this
 * generator, because the API image has no JavaScript runtime. That is a
 * reasonable trade and a standing drift risk: change `generate.js`, forget to
 * re-export, and the demo population silently stops matching its source.
 *
 * This test closes the loop. It regenerates with the recorded inputs,
 * re-compresses exactly as the exporter does, and compares the digest with the
 * constant the seeder verifies against. A failure means one command:
 *
 *   node tools/export-synthetic-university.mjs
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

import {
  GZIP_OS_BYTE_OFFSET,
  GZIP_OS_UNKNOWN,
  gzipCanonicalJson,
} from "../src/synthetic-university/archive.js";
import {
  DEFAULT_NOW,
  generateUniverse,
  validateUniverse,
} from "../src/synthetic-university/index.js";

const REPOSITORY_ROOT = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const ARCHIVE_PATH = join(
  REPOSITORY_ROOT,
  "apps/api/assets/demo/synthetic-university-v1.json.gz",
);
const CONSTANTS_PATH = join(
  REPOSITORY_ROOT,
  "apps/api/src/audentra/infrastructure/seeding/synthetic_university_asset.py",
);

/** Reads one `NAME: Final = value` constant out of the generated Python. */
function constant(source, name) {
  const match = source.match(new RegExp(`^${name}: Final = (.+)$`, "m"));
  assert.ok(match, `${name} is missing from synthetic_university_asset.py`);
  return match[1].replace(/^"|"$/g, "");
}

describe("packaged synthetic university archive", () => {
  const constants = readFileSync(CONSTANTS_PATH, "utf8");
  const archive = readFileSync(ARCHIVE_PATH);

  it("matches the digest the Python seeder verifies", () => {
    const digest = createHash("sha256").update(archive).digest("hex");
    assert.equal(digest, constant(constants, "ASSET_SHA256"));
    assert.equal(String(archive.length), constant(constants, "ASSET_SIZE_BYTES"));
    assert.equal(archive[GZIP_OS_BYTE_OFFSET], GZIP_OS_UNKNOWN);
  });

  it("is byte-identical to a fresh export from the recorded inputs", () => {
    const seed = Number.parseInt(constant(constants, "UNIVERSE_SEED"), 10);
    const studentCount = Number.parseInt(constant(constants, "UNIVERSE_STUDENT_COUNT"), 10);
    assert.equal(constant(constants, "UNIVERSE_GENERATED_FOR"), DEFAULT_NOW);

    const universe = generateUniverse({ seed, studentCount, now: DEFAULT_NOW });
    assert.deepEqual(validateUniverse(universe), []);
    const rebuilt = gzipCanonicalJson(universe);

    assert.equal(
      createHash("sha256").update(rebuilt).digest("hex"),
      constant(constants, "ASSET_SHA256"),
      "the archive is stale; run `node tools/export-synthetic-university.mjs`",
    );
  });
});
