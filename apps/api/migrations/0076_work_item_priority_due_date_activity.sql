-- Operational metadata edits use the existing work command, audit and outbox.
ALTER TABLE staff_work_log DROP CONSTRAINT staff_work_log_action_check;
ALTER TABLE staff_work_log ADD CONSTRAINT staff_work_log_action_check CHECK (action IN (
 'created','status_changed','assigned','escalated','commented','document_decided',
 'student_preferences_updated','channel_selected','interaction_started',
 'communication_recorded','outcome_recorded','follow_up_scheduled','blocked',
 'cancelled','ai_refresh_requested','ai_outcome_updated','ai_task_insight_updated',
 'student_summary_updated','call_recording_uploaded','call_transcription_updated',
 'scheduled_rule_matched','outreach_draft_saved','priority_changed','due_date_changed'
));
