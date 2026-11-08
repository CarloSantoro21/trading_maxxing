"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { GoalsView } from "@/components/goals/GoalsView";

export default function GoalsPage() {
  return (
    <RequireAuth>
      <GoalsView />
    </RequireAuth>
  );
}
