import { describe, expect, it } from "vitest";
import {
  isSkippableOnboardingStep,
  validateOnboardingStepData,
} from "../src/portal/onboarding-policy";

describe("onboarding policy", () => {
  it("allows optional life-planning and deposit steps to be deferred", () => {
    expect(isSkippableOnboardingStep("offer")).toBe(false);
    expect(isSkippableOnboardingStep("housing")).toBe(false);
    expect(isSkippableOnboardingStep("campus_life")).toBe(true);
    expect(isSkippableOnboardingStep("deposit")).toBe(true);
    expect(isSkippableOnboardingStep("about_you")).toBe(false);
    expect(isSkippableOnboardingStep("emergency_contacts")).toBe(false);
  });

  it("requires only the top-level housing path", () => {
    expect(() =>
      validateOnboardingStepData("housing", {}),
    ).toThrow(/housing preference/i);

    expect(() =>
      validateOnboardingStepData("housing", {
        housingPreference: "on_campus",
        roommateMatching: "known_roommate",
      }),
    ).not.toThrow();
  });

  it("requires the student to enter an emergency contact", () => {
    expect(() =>
      validateOnboardingStepData("emergency_contacts", {
        emergencyContacts: [],
      }),
    ).toThrow(/emergency contact/i);

    expect(() =>
      validateOnboardingStepData("emergency_contacts", {
        emergencyContacts: [
          {
            fullName: "Jordan Morgan",
            relationship: "parent",
            mobilePhone: "+15550100300",
          },
        ],
      }),
    ).not.toThrow();
  });

  it("does not require payment when the student chooses a later path", () => {
    expect(() =>
      validateOnboardingStepData("deposit", {
        depositChoice: "pay_later",
      }),
    ).not.toThrow();
    expect(() =>
      validateOnboardingStepData("deposit", {
        depositChoice: "waiver_or_deferral",
      }),
    ).not.toThrow();
  });
});
