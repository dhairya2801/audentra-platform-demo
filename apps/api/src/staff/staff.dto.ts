import {
  IsBoolean,
  IsIn,
  IsInt,
  IsOptional,
  IsString,
  IsUUID,
  MaxLength,
  Min,
  MinLength,
  ValidateIf,
} from "class-validator";

export class UpdateStaffWorkItemDto {
  @IsInt()
  @Min(1)
  expectedVersion!: number;

  @IsOptional()
  @IsIn(["todo", "in_progress", "done"])
  status?: "todo" | "in_progress" | "done";

  @IsOptional()
  @ValidateIf((_object, value) => value !== null)
  @IsUUID()
  assigneeId?: string | null;

  @IsOptional()
  @IsBoolean()
  escalated?: boolean;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(500)
  note?: string;
}

export class UpdateStaffStudentPreferencesDto {
  @IsInt()
  @Min(1)
  expectedOnboardingVersion!: number;

  @IsInt()
  @Min(1)
  expectedProfileVersion!: number;

  @IsIn(["email", "sms"])
  communicationPreference!: "email" | "sms";

  @IsIn([
    "on_campus",
    "off_campus",
    "commuting",
    "undecided",
    "family",
  ])
  housingPreference!:
    | "on_campus"
    | "off_campus"
    | "commuting"
    | "undecided"
    | "family";

  @IsIn(["not_now", "housing", "academic", "both"])
  accommodationInterest!: "not_now" | "housing" | "academic" | "both";

  @IsIn(["home_address_review", "document_upload", "advisor_review"])
  residencyVerificationPath!:
    | "home_address_review"
    | "document_upload"
    | "advisor_review";

  @IsBoolean()
  notifyStudent!: boolean;

  @IsOptional()
  @IsString()
  @MinLength(1)
  @MaxLength(500)
  note?: string;
}

export class ReviewStaffDocumentDto {
  @IsUUID()
  workItemId!: string;

  @IsInt()
  @Min(1)
  expectedWorkItemVersion!: number;

  @IsIn(["accepted", "rejected"])
  decision!: "accepted" | "rejected";

  @IsString()
  @MinLength(3)
  @MaxLength(500)
  note!: string;

  @IsBoolean()
  notifyStudent!: boolean;
}
