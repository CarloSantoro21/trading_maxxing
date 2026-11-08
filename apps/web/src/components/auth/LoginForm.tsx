"use client";

import type { SupabaseClient } from "@supabase/supabase-js";
import { useId, useState } from "react";
import { z } from "zod";
import {
  errorClass,
  inputClass,
  labelClass,
  primaryButtonClass,
} from "@/components/ui/styles";

export const loginSchema = z.object({
  email: z.email({ error: "Escribe un correo válido." }),
  password: z.string().min(6, { error: "La contraseña debe tener al menos 6 caracteres." }),
});

type LoginErrors = Partial<Record<"email" | "password" | "form", string>>;

/** Traduce los errores comunes de Supabase Auth a mensajes en español. */
export function authErrorMessage(message: string | undefined): string {
  const m = (message ?? "").toLowerCase();
  if (m.includes("invalid login credentials")) return "Correo o contraseña incorrectos.";
  if (m.includes("email not confirmed")) return "Confirma tu correo antes de iniciar sesión.";
  if (m.includes("fetch") || m.includes("network")) {
    return "No se pudo conectar con Supabase. ¿Está corriendo `supabase start`?";
  }
  return "No se pudo iniciar sesión. Intenta de nuevo.";
}

export function LoginForm({
  supabase,
  onSuccess,
}: {
  supabase: SupabaseClient;
  onSuccess: (userId: string) => void | Promise<void>;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<LoginErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const id = useId();

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = loginSchema.safeParse({ email: email.trim(), password });
    if (!parsed.success) {
      const next: LoginErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as "email" | "password";
        next[key] ??= issue.message;
      }
      setErrors(next);
      return;
    }

    setErrors({});
    setSubmitting(true);
    try {
      const { data, error } = await supabase.auth.signInWithPassword(parsed.data);
      if (error || !data.user) {
        setErrors({ form: authErrorMessage(error?.message) });
        return;
      }
      await onSuccess(data.user.id);
    } catch (err) {
      setErrors({ form: authErrorMessage(err instanceof Error ? err.message : undefined) });
    } finally {
      setSubmitting(false);
    }
  }

  const emailId = `${id}-email`;
  const passwordId = `${id}-password`;

  return (
    <form noValidate onSubmit={handleSubmit} className="space-y-5" aria-label="Iniciar sesión">
      {errors.form && (
        <div
          role="alert"
          className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
        >
          {errors.form}
        </div>
      )}

      <div>
        <label htmlFor={emailId} className={labelClass}>
          Correo electrónico
        </label>
        <input
          id={emailId}
          name="email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={errors.email ? true : undefined}
          aria-describedby={errors.email ? `${emailId}-error` : undefined}
          className={`${inputClass} mt-1`}
        />
        {errors.email && (
          <p id={`${emailId}-error`} className={errorClass}>
            {errors.email}
          </p>
        )}
      </div>

      <div>
        <label htmlFor={passwordId} className={labelClass}>
          Contraseña
        </label>
        <input
          id={passwordId}
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={errors.password ? true : undefined}
          aria-describedby={errors.password ? `${passwordId}-error` : undefined}
          className={`${inputClass} mt-1`}
        />
        {errors.password && (
          <p id={`${passwordId}-error`} className={errorClass}>
            {errors.password}
          </p>
        )}
      </div>

      <button type="submit" disabled={submitting} className={`${primaryButtonClass} w-full`}>
        {submitting ? "Entrando…" : "Iniciar sesión"}
      </button>
    </form>
  );
}
