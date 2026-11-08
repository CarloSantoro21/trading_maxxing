"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { DashboardView } from "@/components/dashboard/DashboardView";
import { AppShell } from "@/components/nav/AppShell";

export default function DashboardPage() {
  return (
    <RequireAuth>
      <AppShell>
        <DashboardView />
      </AppShell>
    </RequireAuth>
  );
}
