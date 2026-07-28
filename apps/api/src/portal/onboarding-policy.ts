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
  "family_permissions",
  "review_and_sign",
  "deposit",
];

const skippableOnboardingSteps = new Set<OnboardingStep>([
  "campus_life",
  "deposit",
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
        !data.personalEmail ||
        !data.mobilePhone ||
        !data.citizenshipStatus ||
        !data.streetAddress ||
        !data.city ||
        !data.stateOrProvince ||
        !data.postalCode ||
        !data.country ||
        !data.communicationPreference ||
        !data.residencyStatus
      ) {
        invalid(
          "Enter your legal and preferred name, personal contact details, citizenship status, and permanent home address",
        );
      }
      return;
    case "housing":
      if (!data.housingPreference) invalid("Choose a housing preference");
      return;
    case "campus_life":
      return;
    case "emergency_contacts":
      if (!data.emergencyContacts || data.emergencyContacts.length === 0) {
        invalid("Add at least one emergency contact");
      }
      return;
    case "family_permissions":
      return;
    case "review_and_sign":
      if (
        !data.signatureFullName ||
        !data.signatureMethod ||
        !data.signatureConsent ||
        !data.signedDocumentIds ||
        data.signedDocumentIds.length === 0 ||
        (data.signatureMethod === "drawn" && !data.signatureImageData)
      ) {
        invalid(
          "Review the document packet, choose a signature method, and provide your electronic signature",
        );
      }
      return;
    case "deposit":
      if (!data.depositChoice) {
        invalid("Choose how you would like to handle the enrollment deposit");
      }
  }
}
