"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { BacktestsView } from "@/components/backtests/BacktestsView";
import { AppShell } from "@/components/nav/AppShell";

export default function BacktestsPage() {
  return (
    <RequireAuth>
      <AppShell>
        <BacktestsView />
      </AppShell>
    </RequireAuth>
  );
}
