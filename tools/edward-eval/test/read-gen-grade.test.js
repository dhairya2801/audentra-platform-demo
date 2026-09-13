import test from "node:test";
import assert from "node:assert/strict";
import {
  resolveTemplate,
  resolveQuestion,
  gradeTurn,
  classifyFailure,
  answerCorpus,
} from "../read-gen/grade.mjs";

const truth = {
  students: {
    "SYN-000001": {
      id: "11111111-1111-4111-8111-111111111111",
      primaryAdviser: { name: "Ada Ashgrove", namePattern: "(?:Ada Ashgrove|Ashgrove)", email: "ada@aster.example.edu" },
      work: { openCount: 1234, assignees: ["Greta Radcliffe", "Yusuf Crane"], open: [{ key: "AST-1", assignee: "Greta Radcliffe" }] },
      appointments: { next: { date: "2026-09-04" } },
      caseloadPeers: ["Bianca Dunmire"],
    },
  },
  cohorts: { sameName: { Omar: { entries: [{ id: "a" }, { id: "b" }] } } },
};

test("templates resolve every form", () => {
  const S = "students.SYN-000001";
  assert.equal(resolveTemplate(`{{gt:${S}.primaryAdviser.name}}`, truth), "Ada Ashgrove");
  assert.equal(resolveTemplate(`{{re:${S}.primaryAdviser.namePattern}}`, truth), "(?:Ada Ashgrove|Ashgrove)");
  assert.equal(resolveTemplate(`{{num:${S}.work.openCount}}`, truth), "\\b1,?234\\b");
  assert.match("about 1,236 items", new RegExp(resolveTemplate(`{{num~:${S}.work.openCount}}`, truth)));
  const date = new RegExp(resolveTemplate(`{{date:${S}.appointments.next.date}}`, truth), "i");
  for (const text of ["2026-09-04", "Sep 4", "September 4, 2026", "4 September", "9/4", "09/04"]) assert.match(text, date, text);
  assert.match("owned by Yusuf Crane", new RegExp(resolveTemplate(`{{any:${S}.work.assignees}}`, truth)));
  assert.match("Greta Radcliffe", new RegExp(resolveTemplate(`{{any:${S}.work.open|assignee}}`, truth)));
  assert.match("Greta Radcliffe and Yusuf Crane", new RegExp(resolveTemplate(`{{all:${S}.work.assignees}}`, truth)));
  assert.doesNotMatch("Greta Radcliffe alone", new RegExp(resolveTemplate(`{{all:${S}.work.assignees}}`, truth)));
  assert.equal(resolveQuestion(`who is {{gt:${S}.primaryAdviser.name}}`, truth), "who is Ada Ashgrove");
  assert.throws(() => resolveTemplate("{{gt:students.nope.x}}", truth), /Ground truth missing/);
});

test("gradeTurn: facts, forbidden, entity, tools, actions", () => {
  const S = "students.SYN-000001";
  const payload = {
    message: "Your adviser is Ada Ashgrove (ada@aster.example.edu).",
    blocks: [],
    resolvedStudent: { id: "11111111-1111-4111-8111-111111111111" },
    actionIntents: [],
  };
  const trace = { classification: { requestType: "advising" }, toolCalls: [{ tool: "getStudentAdvising", status: "available" }] };
  const good = gradeTurn(
    {
      expect: {
        requestTypes: ["advising"],
        requiredTools: ["getStudentAdvising"],
        resolvedStudentId: `gt:${S}.id`,
        facts: [{ desc: "adviser", pattern: `{{re:${S}.primaryAdviser.namePattern}}` }],
        forbidden: [{ desc: "peer", pattern: `{{any:${S}.caseloadPeers}}` }],
        actionIntents: "none",
      },
    },
    payload,
    trace,
    truth,
    "staff",
  );
  assert.equal(good.grade, "PASS", JSON.stringify(good));

  const partial = gradeTurn(
    { expect: { requestTypes: ["documents"], facts: [{ desc: "soft", pattern: "nope", critical: false }] } },
    payload,
    trace,
    truth,
    "student",
  );
  assert.equal(partial.grade, "PARTIAL");
  assert.equal(partial.softMisses.length, 2);

  const bad = gradeTurn(
    {
      expect: {
        resolvedStudentId: null,
        forbidden: [{ desc: "email", pattern: "@aster\\.example\\.edu" }],
        forbiddenTools: ["getStudentAdvising"],
        factGroups: [[{ desc: "x", pattern: "zzz" }], [{ desc: "y", pattern: "yyy" }]],
      },
    },
    payload,
    trace,
    truth,
    "staff",
  );
  assert.equal(bad.grade, "FAIL");
  assert.deepEqual(
    bad.failures.map((f) => f.kind).sort(),
    ["arbitrary_resolution", "fact_missing", "forbidden_claim", "forbidden_tool_called"],
  );
  assert.equal(classifyFailure({ expect: bad ? {} : {} }, bad.failures, trace, "", truth), "hallucination");

  const within = gradeTurn({ expect: { resolvedStudentIn: "cohorts.sameName.Omar.entries" } }, { message: "", resolvedStudent: { id: "b" } }, trace, truth, "staff");
  assert.equal(within.grade, "PASS");
  const outside = gradeTurn({ expect: { resolvedStudentIn: "cohorts.sameName.Omar.entries" } }, { message: "", resolvedStudent: null }, trace, truth, "staff");
  assert.equal(outside.failures[0].kind, "not_resolved");

  const propose = gradeTurn({ expect: { proposeOrClarify: true } }, { message: "Done." }, trace, truth, "staff");
  assert.equal(propose.failures[0].kind, "action_not_proposed");
  const asks = gradeTurn({ expect: { proposeOrClarify: true } }, { message: "Which student?" }, trace, truth, "staff");
  assert.equal(asks.grade, "PASS");
  const readFail = gradeTurn({ expect: {} }, { message: "I couldn't read your documents right now." }, trace, truth, "student");
  assert.equal(readFail.failures[0].kind, "read_failed");
});

test("classifyFailure: composition vs query, tool, entity, action", () => {
  const trace = { classification: { requestType: "x" }, toolCalls: [{ tool: "getStudentDocuments", status: "available" }] };
  const missing = [{ kind: "fact_missing", detail: "d", pattern: "transcript" }];
  assert.equal(classifyFailure({ expect: {} }, missing, trace, "transcript-x.pdf under review", {}), "composition");
  assert.equal(classifyFailure({ expect: {} }, missing, trace, "nothing here", {}), "query");
  assert.equal(classifyFailure({ expect: { requestTypes: ["y"] } }, missing, trace, "", {}), "routing");
  assert.equal(classifyFailure({ expect: {} }, [{ kind: "tool_not_called", detail: "t" }], trace, "", {}), "tool");
  assert.equal(classifyFailure({ expect: {} }, [{ kind: "wrong_student", detail: "t" }], trace, "", {}), "entity");
  assert.equal(classifyFailure({ expect: {} }, [{ kind: "action_proposed", detail: "t" }], trace, "", {}), "action");
  assert.equal(classifyFailure({ expect: {} }, missing, { toolCalls: [{ tool: "t", status: "timeout" }] }, "", {}), "timeout");
  assert.equal(classifyFailure({ expect: {}, productGap: true }, missing, trace, "", {}), "product_data");
});

test("answerCorpus includes block text", () => {
  const corpus = answerCorpus({ message: "hi", blocks: [{ fallbackText: "fb" }, { items: [{ text: "it" }] }, { rows: [{ a: "row" }] }] });
  for (const piece of ["hi", "fb", "it", "row"]) assert.match(corpus, new RegExp(piece));
});


test("typographic apostrophes preserve both required denials and forbidden claims", () => {
  const turn={expect:{facts:[{desc:"denial",pattern:"can't access"}],forbidden:[{desc:"disclosure",pattern:"it's accepted"}]}};
  const safe=gradeTurn(turn,{message:"I can’t access another student's records."},{},{},"student");
  assert.equal(safe.grade,"PASS");
  const unsafe=gradeTurn(turn,{message:"I can access it and it’s accepted."},{},{},"student");
  assert.equal(unsafe.grade,"FAIL");
  assert.ok(unsafe.failures.some(f=>f.kind==="fact_missing"));
  assert.ok(unsafe.failures.some(f=>f.kind==="forbidden_claim"));
});
