# Agentic Flows and Cost Controls

## 1. Agent role

VV agents explain, summarize, draft, and recommend. Deterministic application
logic remains responsible for official state and workflow.

### Permitted agent responsibilities

- explain why a requirement applies;
- answer questions grounded in institution policy;
- summarize blockers in plain language;
- draft a student support case;
- draft staff outreach;
- classify a document or extract candidate metadata;
- summarize a student case for assigned staff;
- recommend an approved intervention playbook;
- narrate already-calculated cohort metrics.

### Prohibited agent responsibilities

- determine eligibility or official enrollment status;
- grant or revoke FERPA permissions;
- alter requirement applicability or completion;
- waive holds or requirements;
- mark payments or signatures complete;
- write arbitrary SQL;
- call arbitrary URLs;
- use general CRM credentials;
- send a message without an explicit approved workflow;
- make irreversible or high-impact changes autonomously.

## 2. Agent architecture

```text
Activity/domain event or explicit user request
  -> deterministic trigger policy
  -> agent eligibility + budget check
  -> compact student/cohort snapshot
  -> policy retrieval where required
  -> model call through agent gateway
  -> schema validation and safety checks
  -> explanation, draft, or recommendation record
  -> explicit user/staff action if a write is needed
  -> outcome measurement
```

The browser never invokes a model provider directly. Every request passes
through the application API and agent gateway.

## 3. Agent trigger types

### Explicit interactive trigger

Examples:

```text
student asks "Why do I need this document?"
student asks for help with a rejected upload
counselor requests a case summary
leader requests an explanation of a metric change
```

Explicit triggers have the clearest user intent and should be prioritized over
speculative background runs.

### Event-triggered candidate

Examples:

```text
deadline approaching
repeated safe validation failure
rejected requirement remains unresolved
student explicitly opened help
case moved to counselor review
```

An event creates a candidate. A deterministic policy chooses template help,
agent help, staff review, or no action.

### Scheduled aggregate trigger

Examples:

```text
daily counselor queue summary
weekly cohort bottleneck narrative
morning enrollment leader brief
```

SQL/projection code calculates facts first. The model receives a compact
aggregate and generates one narrative, rather than one run per student.

## 4. Agent gateway contract

Every run is created with:

```text
tenant
feature
actor
subject/student or cohort
trigger reason
allowed tools
maximum tool calls
input token budget
output token budget
maximum duration
data classification
approval policy
prompt version
model-routing policy
```

The gateway:

1. authorizes actor and feature;
2. checks tenant and user budgets;
3. creates an `agent_run`;
4. builds the minimum snapshot;
5. calls only allowlisted tools;
6. validates structured output;
7. records token and cost usage;
8. persists the result according to retention policy;
9. returns a safe explanation/draft/recommendation;
10. records downstream outcome when available.

## 5. Tool design

Initial read tools:

```text
get_student_snapshot
get_requirement_details
get_upcoming_deadlines
get_recent_interventions
search_institution_policy
list_support_options
get_cohort_metric_snapshot
```

Initial draft tools:

```text
draft_support_case
draft_student_message
draft_staff_case_note
recommend_intervention_playbook
```

Tools accept narrow typed inputs and enforce:

- current tenant;
- authorized student/cohort scope;
- actor relationship;
- field-level data minimization;
- response-size limits;
- audit/correlation IDs.

No tool returns an entire student record by default.

## 6. Compact student snapshot

Example:

```json
{
  "snapshot_version": 18,
  "journey_status": "in_progress",
  "completion_percentage": 62,
  "next_deadline": "2026-05-01",
  "blocking_requirements": [
    {
      "code": "identity_document",
      "status": "rejected",
      "reason_code": "image_not_readable",
      "policy_reference": "identity-policy-v4"
    }
  ],
  "safe_engagement_summary": {
    "upload_failures": 2,
    "help_requested": true,
    "days_since_meaningful_action": 3
  },
  "available_support": [
    "document_help",
    "counselor_case"
  ]
}
```

The model does not receive raw clickstream, unrelated profile fields, document
contents, or full message history unless a specific authorized workflow
requires a bounded excerpt.

## 7. Policy retrieval

Institutional policy documents are:

- tenant-scoped;
- versioned;
- chunked with stable source identifiers;
- tagged by campus, term, program, and audience;
- separated from student PII;
- effective-dated;
- removable/re-indexable when policy changes.

Every policy answer includes source references. If authoritative retrieval
returns no suitable source, the agent must say it cannot confirm and offer the
correct support channel.

## 8. Student requirement explanation flow

```text
Student selects "Why is this required?"
  -> API authorizes access to requirement
  -> deterministic requirement details are loaded
  -> relevant effective policy chunks are retrieved
  -> cache lookup uses:
       tenant
       policy version
       requirement code
       status/reason code
       locale
  -> cache hit returns approved explanation
  -> otherwise small model generates short cited explanation
  -> output schema and source references are validated
  -> explanation is shown
  -> agent usage is recorded
```

This flow cannot change the requirement.

## 9. Stuck-student help flow

```text
Safe friction signals enter engagement projection
  -> trigger rule identifies repeated failure
  -> check suppression window and existing case/intervention
  -> display deterministic inline help first
  -> if student explicitly requests more help:
       build compact snapshot
       retrieve relevant policy/support options
       generate explanation or support-case draft
  -> student reviews and chooses next action
```

No background agent runs merely because a student clicked several times.

## 10. Reminder and outreach flow

```text
Deadline rule identifies candidate
  -> confirm requirement is still incomplete
  -> confirm no recent reminder
  -> confirm communication preference/policy
  -> choose:
       standard template
       agent-personalized draft
       staff-review case
  -> create intervention record
  -> send only through approved messaging command
  -> measure delivery and subsequent meaningful action
```

Most reminders use templates. Agent personalization is reserved for complex
multi-blocker cases or approved experiments.

## 11. Document classification flow

```text
Upload is linked to an optional enrollment requirement
  -> requirement supplies expected type as context, never as the answer
  -> document-processing policy selects OCR/classifier if useful
  -> processor returns candidate:
       document type
       extracted fields
       confidence
       quality issues
  -> deterministic validation checks format/range
  -> mismatch between actual contents and expected requirement blocks
     automatic requirement advancement
  -> low-confidence or consequential result goes to human review
  -> authorized verifier accepts/rejects
  -> verified decision updates requirement
```

The AI classification is evidence for review, not the verification itself.

## 12. Counselor case-summary flow

```text
Counselor opens assigned student
  -> API authorizes assignment
  -> case projection returns structured facts
  -> existing summary cache checks case snapshot hash
  -> if unchanged, reuse summary
  -> if changed and counselor requests summary:
       model receives bounded case snapshot
       creates facts/blockers/recommended-next-step structure
  -> counselor may copy/edit into a case note
  -> saving note is a separate audited command
```

## 13. Leader narrative flow

```text
Projectors calculate cohort metrics in SQL
  -> data-quality and suppression rules run
  -> one compact aggregate is prepared
  -> model narrates material changes and caveats
  -> output links back to metric definitions
  -> leader sees narrative plus underlying numbers
```

No student-by-student model calls are needed to generate the leader narrative.
Small cohorts or sensitive dimensions are suppressed according to policy.

## 14. Model routing

Use the least expensive capability that reliably meets the task:

```text
No model       Rules, templates, calculations, workflow state
Small model    Classification, short explanation, simple draft
Larger model   Ambiguous multi-source case or high-value staff analysis
Human          Consequential decision, low confidence, policy ambiguity
```

Routing considers:

- feature;
- data sensitivity;
- ambiguity;
- expected value;
- latency target;
- tenant policy;
- remaining budget;
- prior failure/escalation.

## 15. Initial execution limits

Recommended configurable defaults:

| Feature | Model calls | Tool calls | Output |
|---|---:|---:|---:|
| Requirement explanation | 1 | 2 | Short answer |
| Stuck-student help | 1 | 2 | Explanation or draft |
| Counselor summary | 1 | 3 | Structured summary |
| Leader narrative | 1 per fresh aggregate | 1 | Short narrative |
| Document classification | 1 | 0-1 | Structured candidate |

Multi-step autonomous loops are not enabled in the initial product.

## 16. Cache and deduplication

Cache keys include semantic source versions:

```text
feature
tenant
policy version
requirement/case/cohort snapshot hash
locale
prompt version
output schema version
```

Changing underlying status or policy invalidates the cache. Repeated identical
requests reuse the result and still enforce authorization at read time.

## 17. Usage and cost ledger

`agent_run`:

```text
id
tenant_id
feature
actor_id
student_id or cohort_id
trigger_type
trigger_event_id
trigger_reason
snapshot_version/hash
prompt_version
model
status
started_at
completed_at
```

`model_usage`:

```text
agent_run_id
provider
model
input_tokens
output_tokens
cached_input_tokens
tool_call_count
latency_ms
estimated_cost
rate_card_version
```

`agent_tool_call`:

```text
agent_run_id
tool_name
authorization_scope
arguments_hash
result_status
duration_ms
error_code
```

## 18. Budgets and circuit breakers

Budgets exist at:

- tenant/month;
- feature/day;
- user/hour;
- agent run;
- model tier;
- background batch.

Circuit breakers:

- stop a run at tool/token/time limit;
- disable background features when tenant budget is reached;
- degrade to template/deterministic help;
- prevent retry storms after provider failure;
- disable a prompt/model version when validation failures exceed threshold;
- prohibit model calls when required policy sources are unavailable.

## 19. Outcome tracking

Every agent feature defines an expected measurable outcome.

Examples:

| Feature | Expected outcome |
|---|---|
| Requirement explanation | Requirement resumed/submitted |
| Rejection help | Corrected document uploaded |
| Reminder | Meaningful action before deadline |
| Support draft | Case created with less student effort |
| Counselor summary | Staff action completed faster |
| Leader narrative | Leader opens relevant drill-down/action |

Measure:

```text
cost per active student
cost per useful intervention
cost per completed requirement
cost per resolved blocker
cost per recovered enrollment
no-action rate
human rejection/edit rate
grounding/source failure rate
```

## 20. Agent safety acceptance criteria

An agent feature is not production-ready until:

- allowed tools and data scope are explicit;
- output has a versioned schema;
- policy grounding behavior is tested;
- unauthorized student/tenant access tests pass;
- token, tool, duration, and budget limits are enforced;
- provider failure has deterministic fallback;
- actions are draft/approval-based where consequential;
- model usage and outcome are measurable;
- prompts/outputs follow retention policy;
- the agent cannot mutate official state outside named application commands.
