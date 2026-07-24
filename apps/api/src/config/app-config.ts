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

export interface AppConfig {
  environment: AppEnvironment;
  port: number;
  databaseUrl: string;
  webOrigins: string[];
  authMode: AuthMode;
  demoIds: {
    tenantId: string;
    studentId: string;
    actorId: string;
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
    demoIds: {
      tenantId: environment.DEMO_TENANT_ID ?? DEMO_IDS.tenantId,
      studentId: environment.DEMO_STUDENT_ID ?? DEMO_IDS.studentId,
      actorId: environment.DEMO_ACTOR_ID ?? DEMO_IDS.personId,
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
