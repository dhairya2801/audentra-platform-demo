"""Code-owned board classification shared by filtering and aggregate counts.

The alias `item` always refers to a tenant-filtered staff_work_item.
"""

PROJECT_SQL = """
CASE
 WHEN EXISTS(SELECT 1 FROM university.runtime_link wl
             WHERE wl.tenant_id=item.tenant_id AND wl.runtime_id=item.id
             AND wl.kind='payment_work_item') THEN 'fa-payments'
 WHEN lower(item.component) LIKE '%housing%' THEN 'cl-housing'
 WHEN item.source_type='document' OR item.action_type='document_review' THEN
  CASE WHEN lower(item.component) SIMILAR TO '%(financial|accounts|aid)%'
       THEN 'fa-docs' ELSE 'en-docs' END
 WHEN item.work_type='communication' OR item.action_type='communication_response' THEN
  CASE WHEN lower(item.component) SIMILAR TO '%(financial|accounts|aid)%'
       THEN 'fa-outreach' ELSE 'en-outreach' END
 ELSE 'en-requests'
END
"""
PROJECTS = frozenset(
    {"fa-payments", "fa-docs", "fa-outreach", "en-docs", "en-outreach", "en-requests", "cl-housing"}
)

PROJECT_LABELS = {
    "fa-docs": "Financial Aid / Document review",
    "fa-outreach": "Financial Aid / Outreach",
    "fa-payments": "Financial Aid / Payments",
    "en-docs": "Enrollment / Document review",
    "en-outreach": "Enrollment / Outreach",
    "en-requests": "Enrollment / Student requests",
    "cl-housing": "Campus Life / Housing requests",
}

# Attention follows the same document/payment precedence as the card projection.
# Every linked record is constrained to the work item's tenant.
ATTENTION_SQL = """
CASE
 WHEN EXISTS(SELECT 1 FROM public.document_record d WHERE d.tenant_id=item.tenant_id
             AND d.id=item.source_id AND item.source_type='document')
 THEN EXISTS(SELECT 1 FROM public.document_record d WHERE d.tenant_id=item.tenant_id
             AND d.id=item.source_id AND d.status IN ('under_review','needs_review'))
 WHEN EXISTS(SELECT 1 FROM university.runtime_link l WHERE l.tenant_id=item.tenant_id
             AND l.runtime_id=item.id AND l.kind='payment_work_item')
 THEN EXISTS(SELECT 1 FROM university.runtime_link l JOIN university.payment p
             ON p.tenant_id=l.tenant_id AND p.id=l.world_id
             WHERE l.tenant_id=item.tenant_id AND l.runtime_id=item.id
             AND l.kind='payment_work_item' AND p.status IN ('failed','reversed'))
 ELSE item.status='blocked'
END
"""
