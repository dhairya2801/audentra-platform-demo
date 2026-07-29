import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Inject, Injectable } from "@nestjs/common";
import type {
  StudentDocumentList,
  StudentOnboarding,
} from "@vv/contracts";
import { createSignedOnboardingPdf } from "@vv/document-preprocessing";
import type { AuthContext } from "../auth/auth-context";
import {
  DOCUMENT_STORAGE,
  type DocumentStorage,
} from "./document-storage";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";

const harvardTenantId = "00000000-0000-7000-8000-000000000002";

const onboardingDocumentTemplates = [
  {
    code: "ferpa_release",
    title: "FERPA Information Release",
    fileName: "ferpa-information-release-signed.pdf",
    sourceSuffix: "ferpa-release.pdf",
    signatureBox: { x: 0.098, y: 0.488, width: 0.53, height: 0.054 },
  },
  {
    code: "enrollment_acknowledgment",
    title: "Enrollment Information Acknowledgment",
    fileName: "enrollment-information-acknowledgment-signed.pdf",
    sourceSuffix: "enrollment-acknowledgment.pdf",
    signatureBox: { x: 0.098, y: 0.447, width: 0.53, height: 0.054 },
  },
] as const;

@Injectable()
export class OnboardingSignedDocumentService {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly store: PlatformStore,
    @Inject(DOCUMENT_STORAGE)
    private readonly storage: DocumentStorage,
  ) {}

  async ensure(input: {
    auth: AuthContext;
    onboarding: StudentOnboarding;
    documents: StudentDocumentList;
    requestId: string;
  }): Promise<number> {
    const { onboarding } = input;
    const data = onboarding.data;
    if (
      onboarding.status !== "completed" ||
      !onboarding.completedAt ||
      data.signatureConsent !== true ||
      !data.signatureFullName ||
      !data.signatureMethod ||
      !data.signedDocumentIds?.length
    ) {
      return 0;
    }
    const existing = new Set(
      input.documents.items
        .filter(
          (document) =>
            document.signature?.onboardingVersion === onboarding.version,
        )
        .map((document) => document.signature?.templateCode),
    );
    const requested = new Set(data.signedDocumentIds);
    const prefix =
      input.auth.tenantId === harvardTenantId ? "harvard" : "aster";
    let created = 0;

    for (const template of onboardingDocumentTemplates) {
      if (!requested.has(template.code) || existing.has(template.code)) {
        continue;
      }
      const id = deterministicDocumentId(
        `${input.auth.tenantId}:${input.auth.studentId}:${template.code}:${onboarding.version}`,
      );
      const bytes = await createSignedOnboardingPdf({
        templateBytes: await readOnboardingTemplate(
          `${prefix}-${template.sourceSuffix}`,
        ),
        signerName: data.signatureFullName,
        signatureMethod: data.signatureMethod,
        ...(data.signatureMethod === "drawn"
          ? { signatureImageData: data.signatureImageData }
          : {}),
        signedAt: onboarding.completedAt,
        auditReceipt:
          `onboarding.${onboarding.version}.${template.code}.${input.auth.studentId}`,
        signatureBox: template.signatureBox,
      });
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      const storageKey = [
        input.auth.tenantId,
        input.auth.studentId,
        "signed-onboarding",
        `${id}.pdf`,
      ].join("/");
      await this.storage.put({
        key: storageKey,
        body: bytes,
        contentType: "application/pdf",
        sha256,
      });
      await this.store.saveStudentSignedDocument({
        auth: input.auth,
        document: {
          id,
          templateCode: template.code,
          onboardingVersion: onboarding.version,
          title: template.title,
          fileName: template.fileName,
          sizeBytes: bytes.length,
          storageKey,
          sha256,
          signerName: data.signatureFullName,
          signatureMethod: data.signatureMethod,
          signedAt: onboarding.completedAt,
        },
        requestId: input.requestId,
      });
      created += 1;
    }
    return created;
  }
}

async function readOnboardingTemplate(fileName: string): Promise<Buffer> {
  const roots = [
    process.env.ONBOARDING_DOCUMENT_TEMPLATE_DIR,
    resolve(process.cwd(), "apps", "web", "public", "documents", "onboarding"),
    resolve(process.cwd(), "..", "web", "public", "documents", "onboarding"),
    resolve(
      process.cwd(),
      "..",
      "..",
      "apps",
      "web",
      "public",
      "documents",
      "onboarding",
    ),
  ].filter((root): root is string => Boolean(root));
  let missing: unknown;
  for (const root of roots) {
    try {
      return await readFile(resolve(root, fileName));
    } catch (error) {
      if (
        !(error instanceof Error) ||
        !("code" in error) ||
        error.code !== "ENOENT"
      ) {
        throw error;
      }
      missing = error;
    }
  }
  throw missing ?? new Error(`Missing onboarding template ${fileName}`);
}

function deterministicDocumentId(value: string): string {
  const bytes = createHash("sha256").update(value).digest().subarray(0, 16);
  bytes[6] = (bytes[6]! & 0x0f) | 0x50;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}
