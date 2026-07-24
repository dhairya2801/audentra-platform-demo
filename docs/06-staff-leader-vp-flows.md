# Staff, Leader, and VP Flows

## 1. Shared-data principle

Student, counselor, director, leader, and VP experiences use the same
operational records and domain events.

```text
Student action
  -> domain event
  -> student portal projection
  -> counselor work queue
  -> director team projection
  -> leader cohort projection
  -> VP executive snapshot
```

Future pages must not introduce disconnected front-end constants. During the
dummy-data phase, deterministic scenario generators create synthetic students
and events from which every higher-level metric is derived.

## 2. Personas and scopes

### Counselor

- assigned students/cases;
- requirement blockers and deadlines;
- communication history within scope;
- intervention recommendations;
- approved student detail.

### Director/manager

- counselor team queues;
- workload and response performance;
- requirement bottlenecks;
- escalated cases;
- cohort progress within assigned campus/program.

### Enrollment leader

- funnel by configured dimensions;
- melt risk and intervention performance;
- major operational bottlenecks;
- forecast versus target;
- cross-team/campus comparisons permitted by policy.

### VP of enrollment

- institution-level outcome and forecast;
- strategic funnel movement;
- financial/operational impact where authorized;
- major risk and opportunity narratives;
- drill-down only when role and purpose permit it.

## 3. Counselor daily work queue

### Projection inputs

```text
blocking requirement status
deadline proximity
days since meaningful action
student help request
rejected submission
open case status
recent intervention
assigned counselor/team
```

### Priority calculation

Priority is deterministic and explainable:

```text
critical deadline/hold
  > explicit student help request
  > rejected blocking requirement
  > deadline approaching without progress
  > prolonged inactivity
  > optional engagement opportunity
```

Any statistical risk score is one input, not an unexplained override.

### User flow

```text
Counselor opens work queue
  -> API authorizes assignment/team scope
  -> queue projection returns prioritized cases with reason codes
  -> counselor opens student case
  -> structured timeline and current blockers load
  -> counselor optionally requests agent summary
  -> counselor chooses approved intervention
  -> message/note/task is drafted
  -> counselor reviews and sends/saves
  -> intervention and outcome tracking begin
```

### Events

```text
Audit: student_case.read
Domain: intervention.created.v1
Domain: message.sent.v1
Domain: support.case_status_changed.v1
Agent: case summary/recommendation ledger
```

## 4. Support case lifecycle

Statuses:

```text
new
triaged
assigned
in_progress
waiting_on_student
waiting_on_institution
resolved
closed
```

Flow:

```text
Student/staff/system creates case
  -> deterministic routing assigns category/team
  -> queue projection updates
  -> authorized staff accepts/assigns
  -> notes and actions are recorded
  -> student receives permitted updates
  -> resolution reason is recorded
  -> intervention outcome is measured
```

AI may summarize or recommend a routing category. Assignment and consequential
actions remain policy controlled.

## 5. Intervention playbook flow

```text
Case or trigger candidate exists
  -> rules filter playbooks by tenant, situation, channel, and permission
  -> optional agent ranks/explains remaining playbooks
  -> staff selects or edits recommended action
  -> application validates allowed action
  -> intervention is created
  -> worker sends/schedules action
  -> delivery and response are recorded
  -> outcome window is evaluated
```

Playbook versions are preserved so historical outcomes remain attributable to
the content/rules used at the time.

## 6. Director/team operations flow

```text
Director opens team dashboard
  -> team/campus authorization is checked
  -> director projection returns:
       cases by priority/status
       workload by counselor
       response/service times
       top requirement bottlenecks
       intervention outcomes
  -> director filters to permitted cohort
  -> director drills into aggregate or assigned cases
  -> reassignment/escalation uses audited commands
```

The projection is updated from the same cases, interventions, requirements, and
events used by the counselor view.

## 7. Enrollment leader flow

```text
Leader opens Enrollment Action Center
  -> authorized cohort projection loads
  -> dashboard shows funnel, blockers, deadlines, and interventions
  -> leader selects a bottleneck
  -> aggregate breakdown loads
  -> leader can:
       create operational initiative
       assign staff task
       activate approved playbook
       request aggregate AI narrative
  -> action creates auditable operational records
```

Leader views use aggregates by default. Student-level drill-down requires a
separate authorization purpose and is audited.

## 8. VP executive flow

```text
VP opens executive snapshot
  -> institution-scope authorization is checked
  -> latest validated executive projection loads
  -> view shows:
       current/forecast enrollment
       movement since prior period
       major funnel conversion changes
       top operational blockers
       intervention effectiveness
       data freshness and caveats
  -> optional AI narrative explains already-calculated changes
  -> VP may create strategic follow-up or open authorized aggregate drill-down
```

An AI-generated executive narrative always remains adjacent to the underlying
metrics and definitions.

## 9. Projection hierarchy

### Student projection

```text
next action
completion
deadlines
messages
blocking requirements
```

### Counselor projection

```text
assigned student
priority
priority reason codes
current blockers
last meaningful action
open cases
last intervention
```

### Director projection

```text
team workload
case aging
service level
requirement bottleneck
intervention results
```

### Leader projection

```text
cohort funnel
expected completion
melt/recovery indicators
operational bottlenecks
playbook performance
```

### VP projection

```text
institution forecast
target variance
material changes
strategic risks/opportunities
freshness and confidence
```

## 10. Synthetic data strategy

Seed scenarios produce complete operational histories:

```text
happy_path
missing_financial_aid
international_document_delay
housing_blocker
deposit_failure
identity_document_rejected
family_permission_granted
accessibility_support_needed
inactive_near_deadline
successful_counselor_recovery
```

A seeded cohort generator:

1. creates institutions, terms, programs, staff, and assignments;
2. creates students and admission offers;
3. instantiates journey/requirement versions;
4. produces deterministic events over a timeline;
5. runs projectors;
6. yields internally consistent counselor through VP views.

The same seed produces the same IDs/outcomes when given the same seed value.

## 11. Analytics and suppression

- Aggregates state data freshness and definition version.
- Small-cell suppression prevents inappropriate identification.
- Filters never allow dimensions outside the actor's scope.
- Risk/forecast outputs expose confidence and contributing reason categories.
- Historical metrics preserve the definition/model version used.
- Agent narratives do not invent unavailable metrics.

## 12. Future-role acceptance criteria

- Every displayed count is traceable to operational records.
- A staff user cannot access a student outside permitted assignment/scope.
- Aggregate totals reconcile across hierarchy levels.
- Staff actions create audit and domain events.
- Agent recommendations show reason/source and require approval as configured.
- Scenario data remains internally consistent across all role views.
- Projection freshness and degraded states are visible.

