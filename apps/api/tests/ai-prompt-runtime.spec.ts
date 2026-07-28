import { describe, expect, it } from "vitest";
import {
  VersionedAiPromptRuntime,
  type AiOperation,
  type AiPromptRuntimeConfig,
  type AiPromptRuntimeRepository,
} from "../src/agentic/ai-prompt-runtime";

class MemoryPromptRepository implements AiPromptRuntimeRepository {
  revision = 1;
  loads = 0;
  checkpoints: Array<{ revision: number; checkedAt: string }> = [];

  async getHead() {
    return {
      revision: this.revision,
      updatedAt: `2026-07-27T00:00:0${this.revision}.000Z`,
    };
  }

  async loadPublished(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<Omit<AiPromptRuntimeConfig, "cacheStatus">> {
    this.loads += 1;
    return {
      ...input,
      promptTemplateVersionId: `prompt-${this.revision}`,
      contextPolicyVersionId: `context-${this.revision}`,
      outputSchemaVersionId: `schema-${this.revision}`,
      configRevision: this.revision,
      updatedAt: `2026-07-27T00:00:0${this.revision}.000Z`,
      systemPrompt:
        "Use only the tenant context and return the requested structured result.",
      userPromptTemplate: null,
      contextPolicy: { catalogVersion: this.revision },
      outputSchema: { type: "object" },
      provider: "openrouter",
      model: "openai/gpt-4.1-mini",
      maxOutputTokens: 2_000,
      temperature: 0,
    };
  }

  async checkpoint(input: {
    revision: number;
    checkedAt: string;
  }): Promise<void> {
    this.checkpoints.push(input);
  }
}

describe("VersionedAiPromptRuntime", () => {
  it("checks the authoritative revision on every call and hot reloads changes", async () => {
    const repository = new MemoryPromptRepository();
    const runtime = new VersionedAiPromptRuntime(repository, "test-instance");
    const request = {
      tenantId: "00000000-0000-7000-8000-000000000001",
      operation: "course_exemption_mapping" as const,
    };

    const first = await runtime.resolve(request);
    const second = await runtime.resolve(request);
    repository.revision = 2;
    const changed = await runtime.resolve(request);

    expect(first.cacheStatus).toBe("miss");
    expect(second.cacheStatus).toBe("hit");
    expect(changed.cacheStatus).toBe("reloaded");
    expect(changed.promptTemplateVersionId).toBe("prompt-2");
    expect(repository.loads).toBe(2);
    expect(repository.checkpoints).toHaveLength(3);
  });

  it("coalesces concurrent refreshes into one atomic load", async () => {
    const repository = new MemoryPromptRepository();
    const runtime = new VersionedAiPromptRuntime(repository, "test-instance");
    const request = {
      tenantId: "00000000-0000-7000-8000-000000000001",
      operation: "immunization_compliance" as const,
    };

    await Promise.all([
      runtime.resolve(request),
      runtime.resolve(request),
      runtime.resolve(request),
    ]);

    expect(repository.loads).toBe(1);
  });
});
