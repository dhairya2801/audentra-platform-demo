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
