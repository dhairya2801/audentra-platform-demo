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
 WHEN item.action_type IN ('reachout','outreach','follow_up') THEN
  CASE WHEN lower(item.component) SIMILAR TO '%(financial|accounts|aid)%'
       THEN 'fa-outreach' ELSE 'en-outreach' END
 ELSE 'en-requests'
END
"""
PROJECTS = frozenset(
    {"fa-payments", "fa-docs", "fa-outreach", "en-docs", "en-outreach", "en-requests", "cl-housing"}
)
