# CGC Report

_Generated: 2026-07-24 21:37 UTC_

_Scoped to repository: `/Users/simalozturk/Projects/Projects/VV_codex_v`_


## God Nodes — Highest Fan-In
_These nodes are called from many places. High fan-in increases risk: a change here affects every caller._

| Kind | Name | File | In-degree |
| --- | --- | --- | --- |
|  | rows | platform/postgres-portal.store.ts | 49 |
|  | query | test/dashboard-projector.test.ts | 41 |
|  | api | test/flows.test.js | 38 |
|  | badRequest | src/errors.js | 35 |
|  | snapshot | src/store.js | 35 |
|  | get | documents/document-storage.ts | 33 |
|  | ConflictError | common/api-error.ts | 29 |
|  | request | lib/api-client.ts | 29 |
|  | authorize | support/in-memory-platform.store.ts | 28 |
|  | NotFoundError | common/api-error.ts | 27 |
|  | objectBody | src/validation.js | 21 |
|  | useApiResource | hooks/use-api-resource.ts | 18 |
|  | BadRequestError | common/api-error.ts | 18 |
|  | resolve | auth/demo-identity.resolver.ts | 17 |
|  | exactKeys | src/validation.js | 17 |


## Most Complex Functions
_Cyclomatic complexity > 10 is a refactoring candidate._

| Function | File | Cyclomatic Complexity |
| --- | --- | --- |
| OnboardingFlow | onboarding/page.tsx | 52 |
| parseOutboxEvent | src/event-parser.ts | 34 |
| validateOnboardingStepData | portal/onboarding-policy.ts | 24 |
| EdwardAssistant | components/edward-assistant.tsx | 22 |
| loadAppConfig | config/app-config.ts | 21 |
| PortalShell | components/portal-shell.tsx | 21 |
| ExtractionReview | documents/page.tsx | 21 |
| validateFileBytes | agentic/student-agent.service.ts | 20 |
| uploadDocument | portal/portal.controller.ts | 20 |
| CampusLifePage | campus-life/page.tsx | 19 |
| ActionWidget | components/edward-assistant.tsx | 19 |
| FinancialsPage | financials/page.tsx | 19 |
| acceptAdmissionOffer | platform/postgres-platform.store.ts | 18 |
| validateStateEffectRegistry | src/validate.ts | 18 |
| updateStudentOnboarding | support/in-memory-platform.store.ts | 17 |


## Potential Dead Code
_Functions with zero callers (not guaranteed dead — may be entry points or called via reflection)._

| Function | File |
| --- | --- |
| constructor | activity/activity.controller.ts |
| ingestBatch | activity/activity.controller.ts |
| confirmDocumentExtraction | agentic/student-agent.service.ts |
| constructor | agentic/student-agent.service.ts |
| uploadDocument | agentic/student-agent.service.ts |
| askEdward | agentic/student-ai.gateway.ts |
| constructor | agentic/student-ai.gateway.ts |
| extractStudentDocument | agentic/student-ai.gateway.ts |
| canActivate | auth/auth-context.guard.ts |
| constructor | auth/auth-context.guard.ts |
| constructor | auth/demo-identity.resolver.ts |
| isUuid | auth/demo-identity.resolver.ts |
| constructor | common/api-error.ts |
| constructor | common/api-error.ts |
| constructor | common/api-error.ts |
| constructor | common/api-error.ts |
| constructor | common/api-error.ts |
| requireIdempotencyKey | common/idempotency-key.ts |
| intercept | common/request-context.interceptor.ts |
| createRequestId | config/app-config.ts |


## Suggested Cypher Queries
_Copy these into `execute_cypher_query` to explore further._

### Callers of a specific function
```cypher
MATCH (caller)-[:CALLS]->(fn:Function {name: 'yourFunctionName'})
RETURN caller.name, caller.path LIMIT 20
```

### Class hierarchy for a specific class
```cypher
MATCH path = (c:Class {name: 'YourClass'})-[:INHERITS*]->(parent)
RETURN [n IN nodes(path) | n.name] AS hierarchy
```

### Most-injected Spring beans
```cypher
MATCH ()-[:INJECTS]->(bean:Class)
RETURN bean.name, count(*) AS injection_count
ORDER BY injection_count DESC LIMIT 10
```

### All external library dependencies
```cypher
MATCH (m:MavenModule)-[:USES_LIBRARY]->(lib:ExternalLibrary)
RETURN m.artifact_id, lib.group_id, lib.artifact_id, lib.version
ORDER BY lib.artifact_id
```

### CALLS edges with low confidence (potential mis-resolutions)
```cypher
MATCH (a)-[c:CALLS]->(b)
WHERE c.confidence_label = 'AMBIGUOUS'
RETURN a.name, b.name, c.resolution_tier, a.path LIMIT 20
```
