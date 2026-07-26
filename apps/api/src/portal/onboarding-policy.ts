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
  "housing",
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
      if (
        data.housingPreference === "on_campus" &&
        (!data.housingRoomType ||
          !data.bathroomPreference ||
          !data.roommateMatching ||
          !data.sleepSchedule ||
          !data.studyHabits ||
          !data.roomNoise ||
          !data.cleanliness ||
          !data.guestPreference ||
          !data.temperaturePreference ||
          !data.smokeVapeCompatibility)
      ) {
        invalid("Complete the on-campus room and roommate preferences");
      }
      if (
        data.housingPreference === "off_campus" &&
        !data.offCampusStatus
      ) {
        invalid("Tell us where you are in your off-campus search");
      }
      if (
        data.housingPreference === "commuting" &&
        (!data.commuteMode || !data.commuteDuration)
      ) {
        invalid("Choose your main commute and one-way travel time");
      }
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
      if (!data.signatureFullName) {
        invalid("Type your full legal name to sign the onboarding packet");
      }
      return;
    case "deposit":
      if (!data.depositChoice) {
        invalid("Choose how you would like to handle the enrollment deposit");
      }
  }
}
