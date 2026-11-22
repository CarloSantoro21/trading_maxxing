"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { GoalsView } from "@/components/goals/GoalsView";
import { AppShell } from "@/components/nav/AppShell";

export default function GoalsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <GoalsView />
      </AppShell>
    </RequireAuth>
  );
}
