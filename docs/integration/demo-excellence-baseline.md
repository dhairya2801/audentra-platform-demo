# Demo excellence baseline · 13 September 2026

Work is confined to the existing `audentra-vnext/platform` and `portals` clones.
No donor worktree is used as visual authority; no push or deployment is authorized.

| Repository | Branch | HEAD | Initial status |
| --- | --- | --- | --- |
| platform | integration/audentra-vnext-platform | b01155c1a37dac4dd80308ffa7946e197a11fb86 | Pre-existing edits to docs/integration/README.md and tools/university/run_runtime.py; excluded from this iteration's commits |
| portals | integration/audentra-vnext-portals | 6d31120787e4179bcb1ef2e71036275638776d39 | Clean |

The canonical architecture consists of PostgreSQL public product records and the
tenant-scoped university domain. FinancialPlanService and WorkBoardProjection
are shared by portal APIs, Edward tools and live Atlas. Document decisions are
immutable, outreach drafts are versioned, and portal delivery is a separate
confirmed command. Edward uses the existing Luna read planner and durable action
preview/confirmation gateway. No replacement planner or parallel board store is
needed. Morning Brew is explicitly excluded from institutional evidence.

Read-only browser inspection of https://test.audentra.ai found the Ada student
selector, Vivian Hale staff selector, Concept 4 financial plan and the approved
iframe Task Board. Screenshots and text captures are in the portal's ignored
`artifacts/demo-excellence/reference` folder. The deployed sidebar currently
includes Action center; the requested final hierarchy intentionally removes it
from that primary level. Deployed projects: Financial Aid (Document review,
Outreach, Payments), Enrollment (Document review, Outreach, Student requests),
Campus Life (Housing requests).

Initial vNext counts: FA documents 97, FA outreach 0, payments 4; enrollment
documents 852, outreach 1, requests 162; housing 1. These are whole-queue totals.
Wren Halloway (SYN-000000) starts with $15,000 posted charges, $3,000 scholarship
credit, $12,000 posted payments and $0 balance; four current courses (12 credits),
an assigned Alder room, active unlimited meals, four accepted documents, no staff
work and no personalized planning inputs. Camila Abernathy is the existing
Academic Advising Center director and default local staff identity.
