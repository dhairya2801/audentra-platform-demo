import { randomUUID } from "node:crypto";

export const APP_CONFIG = Symbol("APP_CONFIG");

export const DEMO_IDS = {
  tenantId: "00000000-0000-7000-8000-000000000001",
  personId: "00000000-0000-7000-8000-000000000100",
  studentId: "00000000-0000-7000-8000-000000000101",
  campusId: "00000000-0000-7000-8000-000000000110",
  academicTermId: "00000000-0000-7000-8000-000000000120",
  programId: "00000000-0000-7000-8000-000000000130",
  offerId: "00000000-0000-7000-8000-000000000201",
  journeyDefinitionVersionId: "00000000-0000-7000-8000-000000000301",
  profileRequirementDefinitionVersionId:
    "00000000-0000-7000-8000-000000000401",
  identityRequirementDefinitionVersionId:
    "00000000-0000-7000-8000-000000000402",
  depositRequirementDefinitionVersionId:
    "00000000-0000-7000-8000-000000000403",
  welcomeMessageId: "00000000-0000-7000-8000-000000000501",
  reminderMessageId: "00000000-0000-7000-8000-000000000502",
  sampleDocumentId: "00000000-0000-7000-8000-000000000601",
  sampleAppointmentId: "00000000-0000-7000-8000-000000000701",
  helpGettingStartedId: "00000000-0000-7000-8000-000000000801",
  helpDocumentsId: "00000000-0000-7000-8000-000000000802",
  helpPaymentsId: "00000000-0000-7000-8000-000000000803",
} as const;

export type AppEnvironment = "development" | "test" | "production";
export type AuthMode = "demo";
export type TranscriptParsingProvider = "openrouter" | "groq";

export interface AppConfig {
  environment: AppEnvironment;
  port: number;
  databaseUrl: string;
  webOrigins: string[];
  authMode: AuthMode;
  /** Shared only with the outbox worker for internal parser commands. */
  documentWorkerToken: string;
  demoIds: {
    tenantId: string;
    studentId: string;
    actorId: string;
  };
  openRouter?: {
    apiKey: string;
    model: string;
    appUrl: string;
    appName: string;
    storeResponses?: boolean;
    documentTimeoutMs?: number;
    documentMaxTokens?: number;
    documentReasoningTokens?: number;
  };
  transcriptParsing?: TranscriptParsingProvider;
  groq?: {
    apiKey: string;
    model: string;
    documentTimeoutMs: number;
    documentMaxTokens: number;
    documentMaxTextCharacters: number;
    reasoningEffort: "low" | "medium" | "high";
  };
  objectStorage?: {
    endpoint: string;
    region: string;
    bucket: string;
    accessKeyId: string;
    secretAccessKey: string;
    forcePathStyle: boolean;
  };
}

function parsePort(value: string | undefined): number {
  const port = Number(value ?? "4000");
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("API_PORT must be an integer between 1 and 65535");
  }
  return port;
}

function parseEnvironment(value: string | undefined): AppEnvironment {
  if (value === undefined || value === "development") return "development";
  if (value === "test" || value === "production") return value;
  throw new Error("NODE_ENV must be development, test, or production");
}

function parseOrigins(value: string | undefined): string[] {
  const origins = (value ?? "http://localhost:3000")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
  if (origins.length === 0) {
    throw new Error("WEB_ORIGIN must contain at least one origin");
  }
  return origins;
}

function parseBoundedInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const number = Number(value ?? fallback);
  if (!Number.isInteger(number)) return fallback;
  return Math.max(minimum, Math.min(maximum, number));
}

function parseTranscriptParsing(
  value: string | undefined,
): TranscriptParsingProvider {
  return value?.trim().toLowerCase() === "groq" ? "groq" : "openrouter";
}

function parseGroqReasoningEffort(
  value: string | undefined,
): "low" | "medium" | "high" {
  const normalized = value?.trim().toLowerCase();
  return normalized === "medium" || normalized === "high"
    ? normalized
    : "low";
}

export function loadAppConfig(
  environment: NodeJS.ProcessEnv = process.env,
): AppConfig {
  const appEnvironment = parseEnvironment(environment.NODE_ENV);
  const authMode = environment.AUTH_MODE ?? "demo";

  if (authMode !== "demo") {
    throw new Error(`Unsupported AUTH_MODE: ${authMode}`);
  }
  if (appEnvironment === "production") {
    throw new Error(
      "The demo identity adapter is disabled in production; configure a production identity adapter first",
    );
  }

  return {
    environment: appEnvironment,
    port: parsePort(environment.API_PORT ?? environment.PORT),
    databaseUrl:
      environment.DATABASE_URL ??
      "postgresql://vv:vv_local_password@localhost:5432/vv_enrollment",
    webOrigins: parseOrigins(environment.WEB_ORIGIN),
    authMode,
    documentWorkerToken:
      environment.DOCUMENT_WORKER_TOKEN?.trim() ||
      "local-development-document-worker-token",
    demoIds: {
      tenantId: environment.DEMO_TENANT_ID ?? DEMO_IDS.tenantId,
      studentId: environment.DEMO_STUDENT_ID ?? DEMO_IDS.studentId,
      actorId: environment.DEMO_ACTOR_ID ?? DEMO_IDS.personId,
    },
    openRouter: {
      apiKey: environment.OPENROUTER_API_KEY?.trim() ?? "",
      model: environment.OPENROUTER_MODEL?.trim() || "openai/gpt-4o-mini",
      appUrl:
        environment.OPENROUTER_APP_URL?.trim() || "http://localhost:3000",
      appName:
        environment.OPENROUTER_APP_NAME?.trim() || "Aster Student Portal",
      storeResponses:
        environment.OPENROUTER_STORE_RESPONSES?.trim().toLowerCase() === "true" ||
        (environment.OPENROUTER_STORE_RESPONSES === undefined &&
          (appEnvironment === "development" || appEnvironment === "test")),
      documentTimeoutMs: parseBoundedInteger(
        environment.OPENROUTER_DOCUMENT_TIMEOUT_MS,
        120_000,
        1_000,
        300_000,
      ),
      documentMaxTokens: parseBoundedInteger(
        environment.OPENROUTER_DOCUMENT_MAX_TOKENS,
        6_000,
        1_200,
        16_000,
      ),
      documentReasoningTokens: parseBoundedInteger(
        environment.OPENROUTER_DOCUMENT_REASONING_TOKENS,
        256,
        0,
        4_096,
      ),
    },
    transcriptParsing: parseTranscriptParsing(
      environment.TRANSCRIPT_PARSING,
    ),
    groq: {
      apiKey: environment.GROQ_API_KEY?.trim() ?? "",
      model:
        environment.GROQ_MODEL?.trim() || "openai/gpt-oss-120b",
      documentTimeoutMs: parseBoundedInteger(
        environment.GROQ_TRANSCRIPT_TIMEOUT_MS,
        60_000,
        1_000,
        300_000,
      ),
      documentMaxTokens: parseBoundedInteger(
        environment.GROQ_TRANSCRIPT_MAX_TOKENS,
        4_000,
        1_200,
        7_000,
      ),
      documentMaxTextCharacters: parseBoundedInteger(
        environment.GROQ_TRANSCRIPT_MAX_TEXT_CHARACTERS,
        10_000,
        2_000,
        20_000,
      ),
      reasoningEffort: parseGroqReasoningEffort(
        environment.GROQ_TRANSCRIPT_REASONING_EFFORT,
      ),
    },
    objectStorage: {
      endpoint:
        environment.OBJECT_STORAGE_ENDPOINT?.trim() || "http://localhost:9000",
      region: environment.OBJECT_STORAGE_REGION?.trim() || "us-east-1",
      bucket: environment.OBJECT_STORAGE_BUCKET?.trim() || "vv-documents",
      accessKeyId:
        environment.OBJECT_STORAGE_ACCESS_KEY?.trim() || "vv_minio",
      secretAccessKey:
        environment.OBJECT_STORAGE_SECRET_KEY?.trim() ||
        "vv_minio_password",
      forcePathStyle:
        environment.OBJECT_STORAGE_FORCE_PATH_STYLE?.trim() !== "false",
    },
  };
}

export function createRequestId(candidate: string | undefined): string {
  if (
    candidate !== undefined &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(candidate)
  ) {
    return candidate;
  }
  return randomUUID();
}
