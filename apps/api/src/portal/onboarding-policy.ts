import type {
  OnboardingStep,
  StudentOnboardingData,
} from "@vv/contracts";
import { BadRequestError } from "../common/api-error";

export const ONBOARDING_STEPS: readonly OnboardingStep[] = [
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

const skippableOnboardingSteps = new Set<OnboardingStep>([
  "housing",
  "campus_life",
]);

export function isSkippableOnboardingStep(step: OnboardingStep): boolean {
  return skippableOnboardingSteps.has(step);
}

export function validateOnboardingStepData(
  step: OnboardingStep,
  data: StudentOnboardingData,
): void {
  const invalid = (message: string): never => {
    throw new BadRequestError("ONBOARDING_STEP_INVALID", message);
  };
  switch (step) {
    case "offer":
      return;
    case "about_you":
      if (
        !data.firstName ||
        !data.lastName ||
        !data.preferredName ||
        !data.mobilePhone ||
        data.legalNameConfirmed !== true ||
        data.contactInformationConfirmed !== true ||
        data.homeAddressConfirmed !== true ||
        !data.communicationPreference ||
        !data.residencyStatus
      ) {
        invalid(
          "Enter your name and mobile phone, then confirm legal name, contact information, home address, communication preference, and residency status",
        );
      }
      return;
    case "housing":
      if (!data.housingPreference) invalid("Choose a housing preference");
      return;
    case "campus_life":
      if (!data.campusInterests || data.campusInterests.length === 0) {
        invalid("Choose at least one campus interest");
      }
      return;
    case "emergency_contacts":
      if (data.emergencyContactConfirmed !== true) {
        invalid("Confirm the emergency contact information");
      }
      return;
    case "other_records":
      if (data.recordsConfirmed !== true) {
        invalid("Confirm the identity, health, and accessibility records");
      }
      return;
    case "family_permissions":
      if (data.familyPermissionsReviewed !== true) {
        invalid("Review the family and FERPA permissions");
      }
      return;
    case "review_and_sign":
      if (data.signatureConfirmed !== true) {
        invalid("Confirm the enrollment review and signature");
      }
      return;
    case "deposit":
      if (data.depositAcknowledged !== true) {
        invalid("Acknowledge the completed enrollment deposit");
      }
  }
}
