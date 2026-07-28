# Multi-tenant student portal deployment

## Current VM preview

The local preview uses path-based tenant resolution so it can host multiple
university experiences without DNS or certificate setup.

```mermaid
flowchart LR
    Browser["Browser"]
    Aster["/aster/*"]
    Harvard["/harvard/*"]
    Web["Student portal web<br/>localhost:3000"]
    Resolver["Tenant path resolver"]
    API["Preview API<br/>localhost:4000"]
    AsterState["Aster state + uploads"]
    HarvardState["Harvard state + uploads"]

    Browser --> Aster
    Browser --> Harvard
    Aster --> Web
    Harvard --> Web
    Web --> Resolver
    Resolver -->|"X-Tenant-Slug: aster"| API
    Resolver -->|"X-Tenant-Slug: harvard"| API
    API --> AsterState
    API --> HarvardState
```

Local routes:

- `http://localhost:3000/aster/onboarding`
- `http://localhost:3000/harvard/onboarding`

The web runtime preserves the tenant prefix across links and redirects. The
preview API validates the tenant slug before choosing a state store. Credential
accounts are scoped to the tenant, and the same email address may belong to
different universities without sharing student records.

The request header is a local-preview transport. It must not be trusted as the
production source of tenant identity.

## Kubernetes production target

Production resolves the tenant from a verified hostname at the edge/API
boundary. All universities share stateless application deployments, while
configuration, student records, prompts, assets, rate limits, and audit trails
remain tenant-scoped.

```mermaid
flowchart TB
    subgraph Internet
      AsterDNS["students.aster.edu"]
      HarvardDNS["students.harvard.edu"]
      OtherDNS["other university domains"]
    end

    subgraph Edge["Public edge"]
      LB["Cloud load balancer"]
      Gateway["Kubernetes Gateway<br/>TLS + hostname routes"]
    end

    subgraph Cluster["Kubernetes cluster — 3 worker VMs"]
      subgraph Node1["Worker VM 1"]
        Web1["web pod"]
        API1["api pod"]
        Worker1["worker pod"]
      end
      subgraph Node2["Worker VM 2"]
        Web2["web pod"]
        API2["api pod"]
        Worker2["worker pod"]
      end
      subgraph Node3["Worker VM 3"]
        Web3["web pod"]
        API3["api pod"]
        Worker3["worker pod"]
      end
      WebService["portal-web Service"]
      APIService["portal-api Service"]
      QueueService["queue client"]
      TenantResolver["Verified hostname resolver"]
      ManifestCache["Versioned experience cache"]
    end

    subgraph Data["Managed data services"]
      Postgres["PostgreSQL<br/>tenant_id + row-level security"]
      ObjectStore["Object storage<br/>tenant-prefixed objects"]
      Redis["Redis / durable queue"]
      Secrets["Secret manager"]
    end

    AsterDNS --> LB
    HarvardDNS --> LB
    OtherDNS --> LB
    LB --> Gateway
    Gateway --> WebService
    Gateway -->|"/api/*"| APIService
    WebService --> Web1
    WebService --> Web2
    WebService --> Web3
    APIService --> API1
    APIService --> API2
    APIService --> API3
    API1 --> TenantResolver
    API2 --> TenantResolver
    API3 --> TenantResolver
    TenantResolver --> ManifestCache
    TenantResolver --> Postgres
    APIService --> ObjectStore
    APIService --> QueueService
    QueueService --> Redis
    Redis --> Worker1
    Redis --> Worker2
    Redis --> Worker3
    Worker1 --> Postgres
    Worker2 --> Postgres
    Worker3 --> Postgres
    Worker1 --> ObjectStore
    Worker2 --> ObjectStore
    Worker3 --> ObjectStore
    Secrets --> APIService
```

## Request and isolation rules

1. DNS and TLS identify the requested university domain.
2. The API resolves that hostname through `tenant_domain`.
3. Authentication must belong to the resolved tenant.
4. Every database operation includes the resolved `tenant_id`.
5. PostgreSQL row-level security provides a second isolation boundary.
6. Object keys start with `tenants/{tenant_id}/`.
7. Experience and prompt caches include tenant and published version.
8. Queue jobs include tenant, student, operation, and configuration version.
9. Rate limits and AI concurrency are enforced per tenant.
10. Logs and audit events record the resolved tenant without recording secrets.

## Workload placement

Start with three web replicas and three API replicas, spread one per VM with
`topologySpreadConstraints`. Run two or three worker replicas and scale them
from queue depth. Use PodDisruptionBudgets, resource requests/limits, readiness
probes, NetworkPolicies, and HorizontalPodAutoscalers.

The number of tenants does not determine the pod count. Aggregate concurrent
traffic, document-processing volume, and AI queue depth determine capacity.

## Migration from local paths to production domains

The UI and API operate on a normalized tenant context. Only the resolver
changes:

```text
Local preview: first URL segment -> tenant slug
Production:    verified Host header -> tenant_domain -> tenant id
```

Path-prefixed URLs can remain available for development, but production routes
should use university domains so cookies, branding, analytics, and security
policies have a natural tenant boundary.
