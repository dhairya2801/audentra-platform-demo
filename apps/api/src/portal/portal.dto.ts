import { Type } from "class-transformer";
import {
  IsArray,
  ArrayMaxSize,
  IsBoolean,
  IsEmail,
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
  AskEdwardInput,
  CompleteStudentOnboardingInput,
  ConfirmStudentDocumentExtractionInput,
  CreateDepositPaymentInput,
  CreateStudentAppointmentInput,
  CreateStudentDocumentInput,
  StudentDocumentCategory,
  HousingPreference,
  HousingResidenceOption,
  OnboardingStep,
  StudentAppointmentType,
  StudentOnboardingData,
  UpdateStudentOnboardingInput,
  UpdateStudentHousingPlanInput,
  UpdateStudentProfileInput,
} from "@vv/contracts";

const onboardingSteps: OnboardingStep[] = [
  "offer",
  "about_you",
  "housing",
  "campus_life",
  "emergency_contacts",
  "family_permissions",
  "review_and_sign",
  "deposit",
];

export class OnboardingEmergencyContactDto {
  @IsString()
  @Length(1, 160)
  fullName!: string;

  @IsIn([
    "parent",
    "guardian",
    "partner",
    "sibling",
    "relative",
    "friend",
    "other",
  ])
  relationship!:
    | "parent"
    | "guardian"
    | "partner"
    | "sibling"
    | "relative"
    | "friend"
    | "other";

  @IsString()
  @Matches(/^\+[1-9][0-9]{7,14}$/)
  mobilePhone!: string;
}

export class OnboardingFamilyPermissionDto {
  @IsString()
  @Length(1, 160)
  fullName!: string;

  @IsIn(["parent", "guardian", "partner", "sponsor", "other"])
  relationship!: "parent" | "guardian" | "partner" | "sponsor" | "other";

  @IsEmail()
  @MaxLength(254)
  email!: string;

  @IsArray()
  @ArrayMaxSize(7)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  scopes!: string[];

  @IsIn([
    "education_and_expenses",
    "academic_planning",
    "billing_and_aid",
    "other",
  ])
  purpose!:
    | "education_and_expenses"
    | "academic_planning"
    | "billing_and_aid"
    | "other";

  @IsIn(["end_first_year", "end_enrollment", "registrar_date"])
  expires!: "end_first_year" | "end_enrollment" | "registrar_date";
}

export class StudentOnboardingDataDto implements StudentOnboardingData {
  @IsOptional()
  @IsString()
  @Length(1, 120)
  firstName?: string;

  @IsOptional()
  @IsString()
  @Length(1, 120)
  lastName?: string;

  @IsOptional()
  @IsString()
  @Length(1, 120)
  preferredName?: string;

  @IsOptional()
  @IsEmail()
  @MaxLength(254)
  personalEmail?: string;

  @IsOptional()
  @IsString()
  @Matches(/^\+[1-9][0-9]{7,14}$/)
  mobilePhone?: string;

  @IsOptional()
  @IsIn([
    "us_citizen",
    "permanent_resident",
    "eligible_noncitizen",
    "international",
  ])
  citizenshipStatus?:
    | "us_citizen"
    | "permanent_resident"
    | "eligible_noncitizen"
    | "international";

  @IsOptional()
  @IsIn(["email", "sms"])
  communicationPreference?: "email" | "sms";

  @IsOptional()
  @IsIn(["domestic", "international"])
  residencyStatus?: "domestic" | "international";

  @IsOptional()
  @IsString()
  @Length(1, 180)
  streetAddress?: string;

  @IsOptional()
  @IsString()
  @MaxLength(180)
  addressLine2?: string;

  @IsOptional()
  @IsString()
  @Length(1, 120)
  city?: string;

  @IsOptional()
  @IsString()
  @Length(1, 120)
  stateOrProvince?: string;

  @IsOptional()
  @IsString()
  @Length(1, 32)
  postalCode?: string;

  @IsOptional()
  @IsString()
  @Length(2, 120)
  country?: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(12)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  supportNeeds?: string[];

  @IsOptional()
  @IsIn(["on_campus", "off_campus", "commuting", "undecided", "family"])
  housingPreference?:
    | "on_campus"
    | "off_campus"
    | "commuting"
    | "undecided"
    | "family";

  @IsOptional()
  @IsIn([
    "aster_residence_hall",
    "aster_apartments",
    "student_village",
  ])
  housingResidenceOption?:
    | "aster_residence_hall"
    | "aster_apartments"
    | "student_village"
    | null;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  housingRoomType?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  bathroomPreference?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  roommateMatching?: string;

  @IsOptional()
  @IsString()
  @Length(2, 160)
  knownRoommateName?: string;

  @IsOptional()
  @IsEmail()
  @MaxLength(254)
  knownRoommateEmail?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  sleepSchedule?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  studyHabits?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  roomNoise?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  cleanliness?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  guestPreference?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  temperaturePreference?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  smokeVapeCompatibility?: string;

  @IsOptional()
  @IsBoolean()
  substanceFreeHousing?: boolean;

  @IsOptional()
  @IsBoolean()
  genderInclusiveHousing?: boolean;

  @IsOptional()
  @IsBoolean()
  accessibleHousingInformation?: boolean;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(6)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  livingLearningCommunities?: string[];

  @IsOptional()
  @IsString()
  @MaxLength(80)
  offCampusStatus?: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(7)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  offCampusResources?: string[];

  @IsOptional()
  @IsString()
  @MaxLength(80)
  commuteMode?: string;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  commuteDuration?: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(8)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  commuterResources?: string[];

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(12)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  campusInterests?: string[];

  @IsOptional()
  @IsString()
  @MaxLength(80)
  socialComfort?: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(7)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  firstMonthGoals?: string[];

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(4)
  @ValidateNested({ each: true })
  @Type(() => OnboardingEmergencyContactDto)
  emergencyContacts?: OnboardingEmergencyContactDto[];

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(4)
  @ValidateNested({ each: true })
  @Type(() => OnboardingFamilyPermissionDto)
  familyPermissions?: OnboardingFamilyPermissionDto[];

  @IsOptional()
  @IsString()
  @Length(2, 240)
  signatureFullName?: string;

  @IsOptional()
  @IsIn(["typed", "drawn"])
  signatureMethod?: "typed" | "drawn";

  @IsOptional()
  @IsString()
  @MaxLength(100_000)
  signatureImageData?: string;

  @IsOptional()
  @IsBoolean()
  signatureConsent?: boolean;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(12)
  @IsString({ each: true })
  @MaxLength(80, { each: true })
  signedDocumentIds?: string[];

  @IsOptional()
  @IsIn(["pay_now", "pay_later", "waiver_or_deferral"])
  depositChoice?: "pay_now" | "pay_later" | "waiver_or_deferral";
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

  @IsOptional()
  @IsBoolean()
  skip?: boolean;
}

export class CompleteStudentOnboardingDto
  implements CompleteStudentOnboardingInput
{
  @IsInt()
  @Min(1)
  expectedVersion!: number;
}

export class UpdateStudentHousingPlanDto
  implements UpdateStudentHousingPlanInput
{
  @IsInt()
  @Min(1)
  expectedVersion!: number;

  @IsIn(["on_campus", "off_campus", "commuting", "undecided", "family"])
  preference!: HousingPreference;

  @IsOptional()
  @IsIn(["aster_residence_hall", "aster_apartments", "student_village"])
  residenceOption?: Exclude<HousingResidenceOption, null>;
}

const documentCategories: StudentDocumentCategory[] = [
  "identity",
  "residency",
  "transcript",
  "financial_aid",
  "health",
  "consent",
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

export class ConfirmStudentDocumentExtractionDto
  implements ConfirmStudentDocumentExtractionInput
{
  @IsArray()
  @ArrayMaxSize(24)
  @IsString({ each: true })
  @Length(1, 80, { each: true })
  acceptedFieldKeys!: string[];
}

export class SelectPaymentPlanDto {
  @IsUUID()
  planId!: string;
}

class EdwardChatMessageDto {
  @IsIn(["user", "assistant"])
  role!: "user" | "assistant";

  @IsString()
  @Length(1, 1_200)
  content!: string;
}

export class AskEdwardDto implements AskEdwardInput {
  @IsString()
  @Length(1, 2_000)
  message!: string;

  @IsString()
  @MaxLength(120)
  pageContext!: string;

  @IsOptional()
  @IsArray()
  @ArrayMaxSize(8)
  @ValidateNested({ each: true })
  @Type(() => EdwardChatMessageDto)
  history?: EdwardChatMessageDto[];
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
