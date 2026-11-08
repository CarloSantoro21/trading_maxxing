import type { SupabaseClient } from "@supabase/supabase-js";
import {
  fractionToPct,
  isRiskLevel,
  pctToFraction,
  type OnboardingFormState,
  type OnboardingValues,
} from "./schema";

/**
 * Lectura y escritura del perfil y las metas con supabase-js.
 *
 * Todas las consultas van con la sesión del usuario (llave anon + JWT), así que las
 * políticas RLS garantizan que solo toque sus propias filas; aquí no se usa service role.
 *
 * El perfil solo guarda nivel de riesgo y capital: la base deriva max_drawdown del nivel
 * (trigger profiles_derive_max_drawdown) y pone los defaults de horizon e instruments.
 */

export interface ProfileRow {
  id: string;
  risk_level: number;
  capital: number | string;
  /** Derivado del nivel por la base; solo se lee. */
  max_drawdown?: number | string | null;
  goals?: { target_return: number | string; deadline: string }[] | null;
}

export class OnboardingSaveError extends Error {
  constructor(message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "OnboardingSaveError";
  }
}

/** Filas para profiles y goals a partir de los valores validados. */
export function toDbRows(userId: string, values: OnboardingValues) {
  return {
    profile: {
      user_id: userId,
      risk_level: values.riskLevel,
      capital: values.capital,
    },
    goals: values.goals.map((g) => ({
      target_return: pctToFraction(g.targetReturnPct),
      deadline: g.deadline,
    })),
  };
}

/** Estado del formulario a partir de un perfil existente (para editarlo). */
export function profileToForm(row: ProfileRow): OnboardingFormState {
  const goals = (row.goals ?? []).map((g) => ({
    targetReturnPct: String(fractionToPct(Number(g.target_return))),
    deadline: g.deadline,
  }));
  return {
    capital: String(Number(row.capital)),
    riskLevel: isRiskLevel(row.risk_level) ? String(row.risk_level) : "3",
    goals: goals.length > 0 ? goals : [{ targetReturnPct: "", deadline: "" }],
  };
}

/** Perfil del usuario con sus metas, o null si todavía no hace el onboarding. */
export async function fetchProfile(
  supabase: SupabaseClient,
  userId: string,
): Promise<ProfileRow | null> {
  const { data, error } = await supabase
    .from("profiles")
    .select("id, risk_level, capital, max_drawdown, goals(target_return, deadline)")
    .eq("user_id", userId)
    .maybeSingle();
  if (error) throw new OnboardingSaveError("No se pudo leer tu perfil.", { cause: error });
  return (data as ProfileRow | null) ?? null;
}

/**
 * Crea o actualiza el perfil (uno por usuario) y reemplaza sus metas.
 *
 * No es transaccional: si falla la escritura de metas, el perfil ya quedó guardado y el
 * usuario puede reintentar; reintentar es idempotente porque las metas se reemplazan.
 */
export async function saveOnboarding(
  supabase: SupabaseClient,
  userId: string,
  values: OnboardingValues,
): Promise<{ profileId: string }> {
  const rows = toDbRows(userId, values);

  const { data: profile, error: profileError } = await supabase
    .from("profiles")
    .upsert(rows.profile, { onConflict: "user_id" })
    .select("id")
    .single();
  if (profileError || !profile) {
    throw new OnboardingSaveError("No se pudo guardar tu perfil.", { cause: profileError });
  }
  const profileId = (profile as { id: string }).id;

  const { error: deleteError } = await supabase.from("goals").delete().eq("profile_id", profileId);
  if (deleteError) {
    throw new OnboardingSaveError("No se pudieron actualizar tus metas.", { cause: deleteError });
  }

  const { error: goalsError } = await supabase
    .from("goals")
    .insert(rows.goals.map((g) => ({ ...g, profile_id: profileId })));
  if (goalsError) {
    throw new OnboardingSaveError("No se pudieron guardar tus metas.", { cause: goalsError });
  }

  return { profileId };
}
