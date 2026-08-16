# Routing-coverage experiment results

| mode | graded cases | strict coverage | experienced coverage | missed asks | gate fired | model calls | tokens | mean ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| augment | 14 | 13 | 14 | 1 | 8 | 2 | 2084 | 62.7 | 5.0 |
| default | 14 | 6 | 8 | 11 | 0 | 2 | 2084 | 69.0 | 3.9 |
| planner | 14 | 13 | 13 | 1 | 7 | 9 | 10469 | 325.8 | 1082.1 |

## Per-case coverage (planned/answered per ask)

| case | mode | classification | asks | missed |
|---|---|---|---|---|
| ss-1 | default | document_status | documents:PA | — |
| sc-1 | default | housing_eligibility +['holds_and_blockers'] | housing:PA | — |
| mdo-1 | default | housing_status +['aid_status'] | housing:PA, aid:PA | — |
| mno-1 | default | housing_eligibility +['holds_and_blockers'] | housing:PA, documents:-A | documents |
| mno-2 | default | campus_life | account:--, campus:PA | account |
| mno-3 | default | document_status | account:--, documents:PA | account |
| nc-1 | default | general_question | aid:--, housing:--, documents:-- | aid, housing, documents |
| um-1 | default | holds_and_blockers | holds:PA, campus:-- | campus |
| id-1 | default | registration_status | registration:PA | — |
| fu-1 | default | housing_status | housing:PA | — |
| am-1 | default | enrollment_state | — | — |
| ds-1 | default | document_status | documents:PA | — |
| un-1 | default | unsupported_or_out_of_scope | — | — |
| tr-1 | default | document_status | account:--, campus:--, documents:PA | account, campus |
| hd-1 | default | housing_eligibility +['holds_and_blockers'] | housing:PA, documents:-A | documents |
| dd-1 | default | missing_documents | documents:PA, deadlines:-- | deadlines |
| ss-1 | augment | document_status | documents:PA | — |
| sc-1 | augment | housing_eligibility +['holds_and_blockers'] | housing:PA | — |
| mdo-1 | augment | housing_status +['aid_status'] | housing:PA, aid:PA | — |
| mno-1 | augment | housing_eligibility +['holds_and_blockers', 'missing_documents'] | housing:PA, documents:PA | — |
| mno-2 | augment | campus_life +['student_account'] | account:PA, campus:PA | — |
| mno-3 | augment | document_status +['student_account'] | account:PA, documents:PA | — |
| nc-1 | augment | general_question +['aid_status', 'housing_status'] | aid:PA, housing:PA, documents:-A | documents |
| um-1 | augment | holds_and_blockers +['campus_life'] | holds:PA, campus:PA | — |
| id-1 | augment | registration_status | registration:PA | — |
| fu-1 | augment | housing_status | housing:PA | — |
| am-1 | augment | enrollment_state | — | — |
| ds-1 | augment | document_status | documents:PA | — |
| un-1 | augment | unsupported_or_out_of_scope | — | — |
| tr-1 | augment | document_status +['student_account', 'campus_life'] | account:PA, campus:PA, documents:PA | — |
| hd-1 | augment | housing_eligibility +['holds_and_blockers', 'missing_documents'] | housing:PA, documents:PA | — |
| dd-1 | augment | missing_documents +['deadlines'] | documents:PA, deadlines:PA | — |
| ss-1 | planner | document_status | documents:PA | — |
| sc-1 | planner | housing_eligibility +['holds_and_blockers'] | housing:PA | — |
| mdo-1 | planner | housing_status +['aid_status'] | housing:PA, aid:PA | — |
| mno-1 | planner | housing_eligibility +['holds_and_blockers', 'missing_documents'] | housing:PA, documents:PA | — |
| mno-2 | planner | student_account +['campus_life'] | account:PA, campus:PA | — |
| mno-3 | planner | document_status +['student_account'] | account:PA, documents:PA | — |
| nc-1 | planner | enrollment_state +['aid_status', 'housing_status'] | aid:PA, housing:PA, documents:-A | documents |
| um-1 | planner | holds_and_blockers +['campus_life'] | holds:PA, campus:PA | — |
| id-1 | planner | registration_status | registration:PA | — |
| fu-1 | planner | housing_status | housing:PA | — |
| am-1 | planner | enrollment_state | — | — |
| ds-1 | planner | document_status | documents:PA | — |
| un-1 | planner | unsupported_or_out_of_scope | — | — |
| tr-1 | planner | student_account +['campus_life', 'document_status'] | account:PA, campus:PA, documents:PA | — |
| hd-1 | planner | housing_eligibility +['holds_and_blockers', 'missing_documents'] | housing:PA, documents:PA | — |
| dd-1 | planner | missing_documents +['deadlines'] | documents:PA, deadlines:PA | — |
