"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { AdminView } from "@/components/admin/AdminView";
import { AppShell } from "@/components/nav/AppShell";

export default function AdminPage() {
  return (
    <RequireAuth>
      <AppShell>
        <AdminView />
      </AppShell>
    </RequireAuth>
  );
}
