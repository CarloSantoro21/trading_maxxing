"use client";

import { HomeRedirect } from "@/components/auth/HomeRedirect";
import { RequireAuth } from "@/components/auth/RequireAuth";

export default function Home() {
  return (
    <RequireAuth>
      <HomeRedirect />
    </RequireAuth>
  );
}
