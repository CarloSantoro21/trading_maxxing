"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { AppShell } from "@/components/nav/AppShell";
import { StrategiesView } from "@/components/strategies/StrategiesView";

export default function StrategiesPage() {
  return (
    <RequireAuth>
      <AppShell>
        <StrategiesView />
      </AppShell>
    </RequireAuth>
  );
}
