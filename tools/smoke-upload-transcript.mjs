import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const toolsDirectory = path.dirname(fileURLToPath(import.meta.url));
const fixturePath = path.join(
  toolsDirectory,
  "fixtures",
  "synthetic-multi-page-transcript.pdf",
);
const requirementId =
  process.env.TRANSCRIPT_REQUIREMENT_ID ??
  "a3bac4b6-418f-4638-8b29-a2efd1d1728b";
const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:4000";

const form = new FormData();
form.append("category", "transcript");
form.append("requirementId", requirementId);
form.append(
  "file",
  new Blob([await readFile(fixturePath)], { type: "application/pdf" }),
  "synthetic-multi-page-transcript.pdf",
);

const response = await fetch(`${apiBaseUrl}/v1/student/documents/upload`, {
  method: "POST",
  headers: {
    "x-demo-student-id": "00000000-0000-7000-8000-000000000101",
    "x-demo-tenant-id": "00000000-0000-7000-8000-000000000001",
    "x-demo-actor-id": "00000000-0000-7000-8000-000000000100",
    "Idempotency-Key": randomUUID(),
  },
  body: form,
});

const payload = await response.json();
console.log(
  JSON.stringify(
    {
      status: response.status,
      documentId: payload.id,
      documentStatus: payload.status,
      extractionStatus: payload.extraction?.status,
      error: payload.error,
    },
    null,
    2,
  ),
);

if (!response.ok) {
  process.exitCode = 1;
}
