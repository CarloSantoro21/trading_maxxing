"use client";

import { Suspense } from "react";
import { LoginContent } from "@/components/auth/LoginContent";

// useSearchParams necesita un límite de Suspense para el export estático.
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginContent />
    </Suspense>
  );
}
