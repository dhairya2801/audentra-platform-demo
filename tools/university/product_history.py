"""Reproducible status snapshots for imported submissions, never invented staff reviews."""

from uuid import UUID, uuid5


async def import_review_snapshots(raw, tenant):
    tenant = UUID(str(tenant))
    rows = await raw.fetch(
        """SELECT id,student_id,requirement_id,status,updated_at
        FROM public.document_record WHERE tenant_id=$1 AND status IN ('accepted','rejected')
        ORDER BY id""",
        tenant,
    )
    for row in rows:
        rejected = row["status"] == "rejected"
        await raw.execute(
            """INSERT INTO public.document_review_decision
            (id,tenant_id,document_id,student_id,requirement_id,reviewer_display_name,
             decision,reason_code,reason_label,student_message,source,decided_at)
            VALUES($1,$2,$3,$4,$5,'Reviewer not recorded',$6,$7,$8,$9,'legacy_backfill',$10)
            ON CONFLICT(tenant_id,document_id) DO NOTHING""",
            uuid5(tenant, "document-status-snapshot:" + str(row["id"])),
            tenant,
            row["id"],
            row["student_id"],
            row["requirement_id"],
            row["status"],
            "legacy_review" if rejected else None,
            "Reason not recorded" if rejected else None,
            "The earlier submission was rejected; its original reviewer guidance was not recorded."
            if rejected
            else "This earlier submission was accepted.",
            row["updated_at"],
        )
