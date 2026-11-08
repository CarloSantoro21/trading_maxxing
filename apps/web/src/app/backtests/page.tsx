"use client";

import { RequireAuth } from "@/components/auth/RequireAuth";
import { BacktestsView } from "@/components/backtests/BacktestsView";

export default function BacktestsPage() {
  return (
    <RequireAuth>
      <BacktestsView />
    </RequireAuth>
  );
}
