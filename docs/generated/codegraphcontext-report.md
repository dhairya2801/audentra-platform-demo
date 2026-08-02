# CodeGraphContext report status

The checked-in report that previously occupied this path was retired during
the FastAPI repository split. It indexed a different local path and deleted
NestJS classes, so keeping its results would misrepresent the current system.

Generate a current local report from the repository root after installing the
CodeGraphContext CLI:

```bash
npm run cgc:index
npm run cgc:report
```

The report is advisory static analysis. The typed State Effect Registry,
OpenTelemetry traces, `audit_event`, and `outbox_event` remain the authoritative
sources for declared behavior and runtime lineage.
