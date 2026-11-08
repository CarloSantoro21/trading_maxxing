"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { AppShell } from "@/components/nav/AppShell";
import { OnboardingLoader } from "@/components/onboarding/OnboardingLoader";

export default function OnboardingPage() {
  return (
    <RequireAuth>
      <AppShell>
        <OnboardingLoader />
      </AppShell>
    </RequireAuth>
  );
}
