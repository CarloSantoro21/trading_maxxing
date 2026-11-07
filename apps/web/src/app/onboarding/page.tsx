"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { OnboardingLoader } from "@/components/onboarding/OnboardingLoader";

export default function OnboardingPage() {
  return (
    <RequireAuth>
      <OnboardingLoader />
    </RequireAuth>
  );
}
