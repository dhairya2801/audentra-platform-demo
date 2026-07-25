import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { OpenRouterGateway } from "../src/openrouter.js";

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("OpenRouterGateway", () => {
  it("uses a bounded portal context for Edward and reports token usage", async () => {
    const requests = [];
    const gateway = new OpenRouterGateway({
      apiKey: "test-key",
      model: "test/edward-model",
      fetch: async (url, init) => {
        requests.push({ url, init, body: JSON.parse(init.body) });
        return jsonResponse({
          model: "test/edward-model",
          choices: [{ message: { content: "Open Enrollment next." } }],
          usage: {
            prompt_tokens: 120,
            completion_tokens: 9,
            total_tokens: 129,
          },
        });
      },
    });

    const result = await gateway.askEdward({
      message: "What should I do next?",
      pageContext: "/dashboard",
      history: [
        { role: "user", content: "Earlier question" },
        { role: "assistant", content: "Earlier answer" },
      ],
      studentContext: {
        preferredName: "Maya",
        programName: "Computer Science",
        termName: "Fall 2026",
        onboardingStatus: "completed",
        enrollmentCompletion: 66,
        nextAction: { title: "Upload ID" },
        unreadMessages: 1,
        documentStatuses: [],
      },
    });

    assert.equal(result.provider, "openrouter");
    assert.equal(result.usage.totalTokens, 129);
    assert.equal(requests.length, 1);
    assert.equal(
      requests[0].url,
      "https://openrouter.ai/api/v1/chat/completions",
    );
    assert.equal(requests[0].body.max_tokens, 420);
    assert.equal(requests[0].body.messages.at(-1).content, "What should I do next?");
    assert.equal(
      requests[0].init.headers.Authorization,
      "Bearer test-key",
    );
  });

  it("sends PDFs through file parsing with a strict structured-output schema", async () => {
    const requests = [];
    const extracted = {
      documentType: "ferpa",
      summary: "Student release authorization.",
      studentName: "Maya Chen",
      institutionName: "Aster University",
      issueDate: null,
      academicTerm: null,
      fields: [
        {
          key: "student_name",
          label: "Student name",
          value: "Maya Chen",
          confidence: 0.97,
        },
        {
          key: "taxpayer_id",
          label: "Taxpayer ID",
          value: "123-45-6789",
          confidence: 0.9,
        },
      ],
      warnings: ["Signature not extracted."],
    };
    const gateway = new OpenRouterGateway({
      apiKey: "test-key",
      fetch: async (_url, init) => {
        const body = JSON.parse(init.body);
        requests.push(body);
        return jsonResponse({
          model: "test/parser",
          choices: [
            { message: { content: JSON.stringify(extracted) } },
          ],
        });
      },
    });

    const result = await gateway.extractStudentDocument({
      fileName: "release.pdf",
      mimeType: "application/pdf",
      bytes: Buffer.from("%PDF-1.7\n%%EOF"),
    });

    assert.equal(result.status, "completed");
    assert.equal(result.documentType, "ferpa");
    assert.equal(result.fields[1].value, "[sensitive value redacted]");
    assert.equal(requests[0].plugins[0].id, "file-parser");
    assert.equal(requests[0].plugins[1].id, "response-healing");
    assert.equal(requests[0].response_format.type, "json_schema");
    assert.equal(requests[0].response_format.json_schema.strict, true);
    const filePart = requests[0].messages[1].content[1];
    assert.equal(filePart.type, "file");
    assert.match(filePart.file.file_data, /^data:application\/pdf;base64,/);
  });

  it("provides useful zero-token behavior when no key is configured", async () => {
    const gateway = new OpenRouterGateway({ apiKey: "" });
    const assistant = await gateway.askEdward({
      message: "How do I upload a document?",
      pageContext: "/dashboard",
      history: [],
      studentContext: {},
    });
    const extraction = await gateway.extractStudentDocument({
      fileName: "transcript.pdf",
      mimeType: "application/pdf",
      bytes: Buffer.from("test"),
    });

    assert.equal(assistant.provider, "guided");
    assert.equal(assistant.usage, null);
    assert.equal(extraction.status, "pending_configuration");
    assert.equal(extraction.documentType, "transcript");
  });
});
