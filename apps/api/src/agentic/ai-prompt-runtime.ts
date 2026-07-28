import { createHash, randomUUID } from "node:crypto";
import { Injectable } from "@nestjs/common";
import { sql } from "drizzle-orm";
import { DatabaseService } from "../database/database.service";

export const AI_PROMPT_RUNTIME = Symbol("AI_PROMPT_RUNTIME");

export const AI_OPERATIONS = [
  "edward_chat",
  "document_classification",
  "document_extraction",
  "transcript_segment_extraction",
  "transcript_merge",
  "course_label_normalization",
  "course_exemption_mapping",
  "immunization_extraction",
  "immunization_compliance",
] as const;

export type AiOperation = (typeof AI_OPERATIONS)[number];
export type PromptCacheStatus = "hit" | "miss" | "reloaded" | "fallback";

export interface AiPromptRuntimeConfig {
  tenantId: string;
  operation: AiOperation;
  promptTemplateVersionId: string | null;
  contextPolicyVersionId: string | null;
  outputSchemaVersionId: string | null;
  configRevision: number;
  updatedAt: string;
  systemPrompt: string;
  userPromptTemplate: string | null;
  contextPolicy: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  provider: "openrouter" | "groq";
  model: string;
  maxOutputTokens: number;
  temperature: number;
  cacheStatus: PromptCacheStatus;
}

export interface AiPromptRuntime {
  resolve(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<AiPromptRuntimeConfig>;
}

export interface AiPromptRuntimeRepository {
  getHead(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<{ revision: number; updatedAt: string } | null>;
  loadPublished(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<Omit<AiPromptRuntimeConfig, "cacheStatus"> | null>;
  checkpoint(input: {
    instanceId: string;
    tenantId: string;
    operation: AiOperation;
    revision: number;
    checkedAt: string;
    loadedAt: string;
  }): Promise<void>;
}

export class VersionedAiPromptRuntime implements AiPromptRuntime {
  private readonly cache = new Map<string, Omit<AiPromptRuntimeConfig, "cacheStatus">>();
  private readonly refreshes = new Map<
    string,
    Promise<Omit<AiPromptRuntimeConfig, "cacheStatus">>
  >();

  constructor(
    private readonly repository: AiPromptRuntimeRepository,
    private readonly instanceId = `api-${process.pid}-${randomUUID()}`,
  ) {}

  async resolve(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<AiPromptRuntimeConfig> {
    const key = `${input.tenantId}:${input.operation}`;
    const checkedAt = new Date().toISOString();
    const head = await this.repository.getHead(input);
    if (!head) {
      throw new Error(
        `No published AI runtime configuration exists for ${input.operation}`,
      );
    }
    const cached = this.cache.get(key);
    if (cached?.configRevision === head.revision) {
      await this.repository.checkpoint({
        ...input,
        instanceId: this.instanceId,
        revision: head.revision,
        checkedAt,
        loadedAt: cached.updatedAt,
      });
      return { ...cached, cacheStatus: "hit" };
    }

    const existingRefresh = this.refreshes.get(key);
    const loaded =
      existingRefresh ??
      this.loadAndValidate(key, input, head.revision).finally(() => {
        this.refreshes.delete(key);
      });
    if (!existingRefresh) this.refreshes.set(key, loaded);
    const resolved = await loaded;
    await this.repository.checkpoint({
      ...input,
      instanceId: this.instanceId,
      revision: resolved.configRevision,
      checkedAt,
      loadedAt: new Date().toISOString(),
    });
    return {
      ...resolved,
      cacheStatus: cached ? "reloaded" : "miss",
    };
  }

  private async loadAndValidate(
    key: string,
    input: { tenantId: string; operation: AiOperation },
    expectedRevision: number,
  ): Promise<Omit<AiPromptRuntimeConfig, "cacheStatus">> {
    const loaded = await this.repository.loadPublished(input);
    if (!loaded || loaded.configRevision !== expectedRevision) {
      throw new Error(
        `AI runtime configuration changed while loading ${input.operation}; retry the call`,
      );
    }
    validateRuntimeConfig(loaded, input);
    const immutable = structuredClone(loaded);
    this.cache.set(key, immutable);
    return immutable;
  }
}

@Injectable()
export class PostgresAiPromptRuntimeRepository
  implements AiPromptRuntimeRepository
{
  constructor(private readonly database: DatabaseService) {}

  async getHead(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<{ revision: number; updatedAt: string } | null> {
    const result = await this.database.db.execute(sql`
      SELECT config_revision, updated_at
      FROM ai_operation_config
      WHERE tenant_id = ${input.tenantId}
        AND operation = ${input.operation}
    `);
    const row = rows<{
      config_revision: string | number;
      updated_at: Date | string;
    }>(result)[0];
    return row
      ? {
          revision: Number(row.config_revision),
          updatedAt: new Date(row.updated_at).toISOString(),
        }
      : null;
  }

  async loadPublished(input: {
    tenantId: string;
    operation: AiOperation;
  }): Promise<Omit<AiPromptRuntimeConfig, "cacheStatus"> | null> {
    const result = await this.database.db.execute(sql`
      SELECT
        config.tenant_id,
        config.operation,
        config.config_revision,
        config.updated_at,
        config.provider,
        config.model,
        config.max_output_tokens,
        config.temperature_milli,
        prompt.id AS prompt_template_version_id,
        prompt.system_prompt,
        prompt.user_prompt_template,
        context.id AS context_policy_version_id,
        context.context_policy,
        output.id AS output_schema_version_id,
        output.output_schema
      FROM ai_operation_config config
      JOIN ai_prompt_template_version prompt
        ON prompt.id = config.prompt_template_version_id
       AND prompt.tenant_id = config.tenant_id
       AND prompt.operation = config.operation
       AND prompt.status = 'published'
      JOIN ai_context_policy_version context
        ON context.id = config.context_policy_version_id
       AND context.tenant_id = config.tenant_id
       AND context.operation = config.operation
       AND context.status = 'published'
      LEFT JOIN ai_output_schema_version output
        ON output.id = config.output_schema_version_id
       AND output.tenant_id = config.tenant_id
       AND output.operation = config.operation
       AND output.status = 'published'
      WHERE config.tenant_id = ${input.tenantId}
        AND config.operation = ${input.operation}
    `);
    const row = rows<Record<string, unknown>>(result)[0];
    if (!row) return null;
    return {
      tenantId: String(row.tenant_id),
      operation: String(row.operation) as AiOperation,
      promptTemplateVersionId: String(row.prompt_template_version_id),
      contextPolicyVersionId: String(row.context_policy_version_id),
      outputSchemaVersionId: row.output_schema_version_id
        ? String(row.output_schema_version_id)
        : null,
      configRevision: Number(row.config_revision),
      updatedAt: new Date(row.updated_at as Date | string).toISOString(),
      systemPrompt: String(row.system_prompt),
      userPromptTemplate:
        typeof row.user_prompt_template === "string"
          ? row.user_prompt_template
          : null,
      contextPolicy: objectValue(row.context_policy),
      outputSchema: row.output_schema ? objectValue(row.output_schema) : null,
      provider: row.provider === "groq" ? "groq" : "openrouter",
      model: String(row.model),
      maxOutputTokens: Number(row.max_output_tokens),
      temperature: Number(row.temperature_milli) / 1000,
    };
  }

  async checkpoint(input: {
    instanceId: string;
    tenantId: string;
    operation: AiOperation;
    revision: number;
    checkedAt: string;
    loadedAt: string;
  }): Promise<void> {
    await this.database.db.execute(sql`
      INSERT INTO ai_runtime_config_checkpoint (
        instance_id,
        tenant_id,
        operation,
        last_seen_revision,
        last_checked_at,
        last_loaded_at
      )
      VALUES (
        ${input.instanceId},
        ${input.tenantId},
        ${input.operation},
        ${input.revision},
        ${new Date(input.checkedAt)},
        ${new Date(input.loadedAt)}
      )
      ON CONFLICT (instance_id, tenant_id, operation)
      DO UPDATE SET
        last_seen_revision = EXCLUDED.last_seen_revision,
        last_checked_at = EXCLUDED.last_checked_at,
        last_loaded_at = CASE
          WHEN ai_runtime_config_checkpoint.last_seen_revision
            IS DISTINCT FROM EXCLUDED.last_seen_revision
          THEN EXCLUDED.last_loaded_at
          ELSE ai_runtime_config_checkpoint.last_loaded_at
        END
    `);
  }
}

export function promptContextSha256(context: unknown): string {
  return createHash("sha256")
    .update(JSON.stringify(context))
    .digest("hex");
}

function validateRuntimeConfig(
  config: Omit<AiPromptRuntimeConfig, "cacheStatus">,
  expected: { tenantId: string; operation: AiOperation },
): void {
  if (
    config.tenantId !== expected.tenantId ||
    config.operation !== expected.operation ||
    !Number.isInteger(config.configRevision) ||
    config.configRevision < 1 ||
    config.systemPrompt.trim().length < 20 ||
    !config.promptTemplateVersionId ||
    !config.contextPolicyVersionId ||
    !config.model.trim() ||
    !Number.isInteger(config.maxOutputTokens) ||
    config.maxOutputTokens < 1
  ) {
    throw new Error(
      `Published AI runtime configuration is invalid for ${expected.operation}`,
    );
  }
}

function rows<T>(result: unknown): T[] {
  if (Array.isArray(result)) return result as T[];
  const candidate = result as { rows?: T[] };
  return candidate.rows ?? [];
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && !Array.isArray(value) && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}
