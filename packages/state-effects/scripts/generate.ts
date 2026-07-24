import { readFile, writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import {
  buildStateEffectGraph,
  renderStateEffectMermaid,
  stateEffects,
  stateFieldOwnership,
  validateStateEffectRegistry,
} from "../src/index";

const repositoryRoot = resolve(import.meta.dirname, "../../..");
const outputDirectory = resolve(repositoryRoot, "docs/generated");
const jsonPath = resolve(outputDirectory, "crm-change-graph.json");
const mermaidPath = resolve(outputDirectory, "crm-change-graph.mmd");
const checkOnly = process.argv.includes("--check");
const issues = validateStateEffectRegistry({
  owners: stateFieldOwnership,
  effects: stateEffects,
});

if (issues.length > 0) {
  for (const issue of issues) {
    console.error(`[${issue.code}] ${issue.message}`);
  }
  process.exitCode = 1;
} else {
  const graph = buildStateEffectGraph({
    owners: stateFieldOwnership,
    effects: stateEffects,
    generatedAt: "generated-from-versioned-registry",
  });
  const json = `${JSON.stringify(graph, null, 2)}\n`;
  const mermaid = renderStateEffectMermaid(graph);
  await mkdir(outputDirectory, { recursive: true });

  if (checkOnly) {
    const [existingJson, existingMermaid] = await Promise.all([
      readFile(jsonPath, "utf8").catch(() => ""),
      readFile(mermaidPath, "utf8").catch(() => ""),
    ]);
    if (existingJson !== json || existingMermaid !== mermaid) {
      console.error(
        "Generated CRM Change Graph is stale. Run: npm run crm:graph",
      );
      process.exitCode = 1;
    } else {
      console.log("CRM Change Graph registry and generated artifacts are valid.");
    }
  } else {
    await Promise.all([
      writeFile(jsonPath, json),
      writeFile(mermaidPath, mermaid),
    ]);
    console.log(`Generated ${jsonPath}`);
    console.log(`Generated ${mermaidPath}`);
  }
}
