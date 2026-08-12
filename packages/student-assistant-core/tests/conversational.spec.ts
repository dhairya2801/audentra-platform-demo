import { describe, expect, it } from "vitest";
import {
  classifyRequestDeterministically,
  normalizeRequestNode,
} from "../src/index";

const classify = (message: string, history: { role: "user" | "assistant"; content: string }[] = []) =>
  classifyRequestDeterministically(
    normalizeRequestNode(message, { history }),
  )?.requestType ?? null;

describe("conversational openers", () => {
  it("recognises greetings without dragging a record read behind them", () => {
    for (const message of [
      "hi",
      "Hi",
      "hii",
      "hey",
      "Hey!",
      "hello",
      "Hello.",
      "yo",
      "howdy",
      "greetings",
      "good morning",
      "Good afternoon",
      "good evening",
      "hi Edward",
      "hello edward",
    ]) {
      expect(classify(message), message).toBe("greeting");
    }
  });

  it("separates asking what Edward is from asking it to do something", () => {
    for (const message of [
      "who are you?",
      "who are you",
      "what are you?",
      "what can you do?",
      "what can you help with?",
      "what can I ask you?",
      "what do you do",
      "how can you help?",
      "what kind of questions can I ask?",
    ]) {
      expect(classify(message), message).toBe("capability_overview");
    }
  });

  it("treats an open request for direction as a question about this student", () => {
    for (const message of [
      "help",
      "help me",
      "I need help",
      "can you help me",
      "I'm not sure where to start",
      "not sure where to start",
      "where do I start?",
      "where should I begin",
      "I don't know where to start",
      "I'm confused",
      "I'm lost",
      "I'm stuck",
      "what should I be doing",
    ]) {
      expect(classify(message), message).toBe("general_help");
    }
  });

  it("does not treat a greeting attached to a real question as small talk", () => {
    // The courtesy is not the question. Answering "hi" here would drop it.
    expect(classify("hi, why is my financial aid incomplete?")).toBe(
      "aid_incomplete_reason",
    );
    expect(classify("hello, what documents am I missing?")).toBe(
      "missing_documents",
    );
    // Not deterministically classified — the planner handles it — but it must
    // not be swallowed as a greeting on the way there.
    expect(classify("good morning, when is orientation?")).not.toBe("greeting");
  });

  it("does not misread ordinary words that merely start like an opener", () => {
    expect(classify("helpful information about my deposit")).not.toBe(
      "general_help",
    );
    expect(classify("what can I do about my hold?")).not.toBe(
      "capability_overview",
    );
    expect(classify("hey, is my transcript accepted?")).toBe("document_status");
  });

  it("does not classify a follow-up turn as an opener", () => {
    // "help" alone mid-conversation is a continuation, not a fresh start.
    expect(
      classify("also help", [
        { role: "user", content: "what do I owe?" },
        { role: "assistant", content: "Your balance is $500." },
      ]),
    ).not.toBe("general_help");
  });
});

describe("follow-up referents", () => {
  const history = [
    { role: "user" as const, content: "Why can't I register for classes?" },
    {
      role: "assistant" as const,
      content:
        "Your immunisation record has not been cleared and you have no completed advising meeting.",
    },
  ];

  it("resolves a pronoun against the student's own previous question", () => {
    const normalized = normalizeRequestNode("How do I fix that?", { history });
    expect(normalized.isFollowUp).toBe(true);
    expect(normalized.resolvedText).toContain("How do I fix that?");
    expect(normalized.resolvedText).toContain("Why can't I register for classes?");
    // The student's literal words stay untouched for classification and display.
    expect(normalized.text).toBe("How do I fix that?");
  });

  it("leaves a self-contained question alone", () => {
    for (const question of [
      "Why is my financial aid incomplete?",
      "What documents am I missing?",
      "When is orientation?",
    ]) {
      const normalized = normalizeRequestNode(question, { history });
      expect(normalized.resolvedText, question).toBe(question);
    }
  });

  it("has nothing to resolve against on a first turn", () => {
    const normalized = normalizeRequestNode("How do I fix that?", { history: [] });
    expect(normalized.isFollowUp).toBe(false);
    expect(normalized.resolvedText).toBe("How do I fix that?");
  });
});

describe("registration clarification", () => {
  it("asks for clarification only when the question really could mean either", async () => {
    const { createStudentAssistantGraph } = await import("../src/graph");
    const { createFakeTools } = await import("./support/fake-tools");

    const ask = async (message: string) => {
      const model = {
        planToolReads: async () => ({
          requestType: "holds_and_blockers",
          additionalRequestTypes: [],
          confidence: 0.9,
          requirementReference: null,
          toolNames: ["getEnrollmentHolds"],
          deadlineWindow: null,
          requestedEntity: null,
          financialAidEntity: null,
          housingEntity: null,
          // The planner reaching for clarification is exactly the input under
          // test: the graph decides whether it is warranted.
          blockerScope: "registration_ambiguous",
          priorityExplanationRequested: false,
          registrationQuestion: true,
        }),
        classifyRequest: async () => ({
          requestType: "holds_and_blockers",
          confidence: 0.9,
          requirementReference: null,
        }),
        composeGroundedResponse: async () => ({ factIds: [], tone: "concise" }),
      };
      const response = await createStudentAssistantGraph({
        tools: createFakeTools(),
        model: model as never,
      }).execute({
        message,
        context: {
          tenantId: "tenant-authoritative",
          studentId: "student-authoritative",
          conversationId: "conversation-authoritative",
          inputMode: "text",
          history: [],
          pageContext: { path: "/enrollment", label: "My Enrollment" },
        },
      });
      return response.message;
    };

    // Genuinely ambiguous: registration named, nothing to disambiguate it.
    expect(await ask("Why am I blocked from registration?")).toMatch(
      /course registration or orientation registration/i,
    );

    // Not ambiguous: the question supplies its own context.
    for (const message of [
      "Can I register even though my financial aid isn't complete?",
      "Why can't I register for classes?",
      "Can I register before my deposit posts?",
      "Do I have a hold stopping me from registering?",
    ]) {
      expect(await ask(message), message).not.toMatch(
        /course registration or orientation registration/i,
      );
    }

    // Never used the word at all.
    expect(await ask("What else do I need?")).not.toMatch(
      /course registration or orientation registration/i,
    );
  });
});
