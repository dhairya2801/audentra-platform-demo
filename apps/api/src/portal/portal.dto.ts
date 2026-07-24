import { Type } from "class-transformer";
import {
  IsArray,
  IsBoolean,
  IsIn,
  IsInt,
  IsISO8601,
  IsOptional,
  IsString,
  IsUUID,
  Length,
  Matches,
  Max,
  MaxLength,
  Min,
  ValidateNested,
} from "class-validator";
import type {
  CompleteStudentOnboardingInput,
  CreateDepositPaymentInput,
  CreateStudentAppointmentInput,
  CreateStudentDocumentInput,
  StudentDocumentCategory,
  OnboardingStep,
  StudentAppointmentType,
  StudentOnboardingData,
  UpdateStudentOnboardingInput,
  UpdateStudentProfileInput,
} from "@vv/contracts";

const onboardingSteps: OnboardingStep[] = [
  "offer",
  "about_you",
  "housing",
  "campus_life",
  "emergency_contacts",
  "other_records",
  "family_permissions",
  "review_and_sign",
  "deposit",
];

export class StudentOnboardingDataDto implements StudentOnboardingData {
  @IsOptional()
  @IsBoolean()
  legalNameConfirmed?: boolean;

  @IsOptional()
  @IsBoolean()
  contactInformationConfirmed?: boolean;

  @IsOptional()
  @IsIn(["email", "sms"])
  communicationPreference?: "email" | "sms";

  @IsOptional()
  @IsIn(["domestic", "international"])
  residencyStatus?: "domestic" | "international";

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  supportNeeds?: string[];

  @IsOptional()
  @IsBoolean()
  homeAddressConfirmed?: boolean;

  @IsOptional()
  @IsIn(["on_campus", "off_campus", "undecided"])
  housingPreference?: "on_campus" | "off_campus" | "undecided";

  @IsOptional()
  @IsArray()
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  campusInterests?: string[];

  @IsOptional()
  @IsBoolean()
  emergencyContactConfirmed?: boolean;

  @IsOptional()
  @IsBoolean()
  recordsConfirmed?: boolean;

  @IsOptional()
  @IsBoolean()
  familyPermissionsReviewed?: boolean;

  @IsOptional()
  @IsBoolean()
  signatureConfirmed?: boolean;

  @IsOptional()
  @IsBoolean()
  depositAcknowledged?: boolean;
}

export class UpdateStudentOnboardingDto
  implements UpdateStudentOnboardingInput
{
  @IsInt()
  @Min(1)
  expectedVersion!: number;

  @IsIn(onboardingSteps)
  currentStep!: OnboardingStep;

  @ValidateNested()
  @Type(() => StudentOnboardingDataDto)
  data!: StudentOnboardingDataDto;
}

export class CompleteStudentOnboardingDto
  implements CompleteStudentOnboardingInput
{
  @IsInt()
  @Min(1)
  expectedVersion!: number;
}

const documentCategories: StudentDocumentCategory[] = [
  "identity",
  "residency",
  "transcript",
  "other",
];

export class CreateStudentDocumentDto
  implements CreateStudentDocumentInput
{
  @IsString()
  @Length(1, 255)
  @Matches(/^(?=.*\S)[^/\\\u0000-\u001F]+$/)
  fileName!: string;

  @IsIn(["application/pdf", "image/jpeg", "image/png"])
  mimeType!: "application/pdf" | "image/jpeg" | "image/png";

  @IsInt()
  @Min(1)
  @Max(10_485_760)
  sizeBytes!: number;

  @IsIn(documentCategories)
  category!: StudentDocumentCategory;
}

const appointmentTypes: StudentAppointmentType[] = [
  "admissions_counseling",
  "financial_aid",
  "enrollment_support",
];

export class CreateStudentAppointmentDto
  implements CreateStudentAppointmentInput
{
  @IsIn(appointmentTypes)
  type!: StudentAppointmentType;

  @IsISO8601({ strict: true, strictSeparator: true })
  startsAt!: string;

  @IsOptional()
  @IsString()
  @MaxLength(500)
  notes?: string;
}

export class CreateDepositPaymentDto implements CreateDepositPaymentInput {
  @IsUUID("all")
  offerId!: string;
}

export class UpdateStudentProfileDto implements UpdateStudentProfileInput {
  @IsInt()
  @Min(1)
  expectedVersion!: number;

  @IsOptional()
  @IsString()
  @Length(1, 120)
  preferredName?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  pronouns?: string | null;

  @IsOptional()
  @IsString()
  @Matches(/^\+?[0-9 ()-]{7,32}$/)
  mobilePhone?: string | null;

  @IsOptional()
  @IsIn(["email", "sms"])
  communicationPreference?: "email" | "sms";
}
