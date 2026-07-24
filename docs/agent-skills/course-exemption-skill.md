# Course Exemption Skill

## Purpose

Turn reviewed transcript evidence into explainable course-exemption
recommendations for a student's selected academic program.

This skill recommends. It never grants credit, changes a degree audit, or makes
an official registrar decision.

## Inputs

- student and tenant identifiers from the authenticated server context;
- selected program and catalog version;
- reviewed transcript courses, exams, scores, grades, credits, terms, and
  source-document identifiers;
- versioned course catalog, prerequisite graph, program requirements, and
  equivalency rules from the database;
- previously approved, denied, or superseded recommendations.

Do not accept catalog rules, student identity, or approval status from the
model or the browser.

## Processing order

1. Confirm the source document belongs to the authenticated student and its
   extracted fields were reviewed.
2. Normalize source labels only. Examples: `AP Calc AB` to
   `AP Calculus AB`, or a transfer-school course code to its canonical source
   code. Preserve the original text.
3. Evaluate active deterministic equivalency rules for the selected catalog
   version.
4. Reject candidates that do not meet minimum score, grade, credits, term,
   accreditation, recency, or content requirements stored in the rule.
5. Create one recommendation per source-credit and target-course pair. Include
   the rule code, source evidence, target course, confidence, rationale, catalog
   version, and why staff review is required.
6. Recompute the student's academic-plan projection:
   - `suggested` does not satisfy prerequisites;
   - `approved` marks the target course exempted and may satisfy downstream
     prerequisites;
   - `denied` changes no course state;
   - a changed source or catalog rule supersedes the old recommendation and
     queues a new review.
7. Write the audit event and outbox event in the same database transaction.

## Model boundary

The model may:

- identify and normalize course or exam labels from an uploaded transcript;
- summarize source-course descriptions when text is present;
- flag ambiguous evidence for human review.

The model must not:

- invent a course, score, grade, credit value, institution, or equivalency;
- choose a target course without a stored rule;
- approve or deny credit;
- infer missing prerequisites;
- expose one student's transcript to another student.

If no stored rule matches, return `no_rule_found` and route the item to a
registrar review queue. Do not use semantic similarity as an automatic
equivalency.

## Required output

```json
{
  "sourceCreditId": "uuid",
  "targetCourseCode": "MATH 151",
  "ruleCode": "AP-CALC-AB-4-MATH151",
  "status": "suggested",
  "confidence": 1,
  "requiresStaffReview": true,
  "rationale": "AP Calculus AB score 5 meets the stored minimum score of 4."
}
```

## Cost controls

- Run deterministic matching without an LLM after extraction.
- Cache normalized source labels by document hash and parser version.
- Send only ambiguous labels—not the full transcript—to any normalization
  model.
- Batch ambiguous courses from one document into one bounded request.
- Never rerun on a page view. Rerun only when the reviewed source, selected
  program, catalog version, or active rule changes.
