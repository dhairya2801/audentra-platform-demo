import { Type } from "class-transformer";
import {
  ArrayMaxSize,
  ArrayMinSize,
  IsArray,
  IsIn,
  IsISO8601,
  IsObject,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Matches,
  ValidateNested,
} from "class-validator";
import type {
  ActivityEventInput,
  ActivityEventName,
} from "@vv/contracts";

const activityEventNames: ActivityEventName[] = [
  "ui.portal_session_started.v1",
  "ui.dashboard_viewed.v1",
  "ui.admission_offer_viewed.v1",
  "ui.admission_decision_started.v1",
  "ui.enrollment_started.v1",
  "ui.enrollment_step_viewed.v1",
  "ui.portal_section_viewed.v1",
  "ui.enrollment_task_viewed.v1",
  "ui.enrollment_task_abandoned.v1",
  "ui.financial_aid_viewed.v1",
  "ui.course_catalog_searched.v1",
  "ui.course_viewed.v1",
  "ui.exemption_reviewed.v1",
  "ui.campus_event_viewed.v1",
  "ui.club_viewed.v1",
  "ui.edward_context_receipts_received.v1",
  "ui.edward_tool_invoked.v1",
  "ui.edward_action_widget_viewed.v1",
  "ui.edward_action_completed.v1",
  "ui.help_opened.v1",
];

export class ActivityEventDto implements ActivityEventInput {
  @IsUUID("all")
  eventId!: string;

  @IsIn(activityEventNames)
  eventName!: ActivityEventName;

  @IsISO8601({ strict: true, strictSeparator: true })
  occurredAt!: string;

  @IsString()
  @Length(8, 128)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/)
  sessionId!: string;

  @IsString()
  @Length(8, 128)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/)
  pageInstanceId!: string;

  @IsOptional()
  @IsString()
  @Length(8, 128)
  @Matches(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/)
  correlationId?: string;

  @IsObject()
  properties!: Record<string, string | number | boolean | null>;
}

export class ActivityEventBatchDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(100)
  @ValidateNested({ each: true })
  @Type(() => ActivityEventDto)
  events!: ActivityEventDto[];
}
