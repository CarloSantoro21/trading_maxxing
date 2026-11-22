import { apiErrorMessage, apiFetch } from "@/lib/api/client";
import {
  fractionToPct,
  isRiskLevel,
  pctToFraction,
  type OnboardingFormState,
  type OnboardingValues,
} from "./schema";

/**
 * Lectura y escritura del perfil y las metas con la API: `GET /api/onboarding` y
 * `PUT /api/onboarding`. La API identifica al usuario por la sesión de Cloudflare Access,
 * así que solo toca sus propias filas.
 *
 * El perfil solo guarda nivel de riesgo y capital: la API deriva max_drawdown del nivel y
 * pone los defaults de horizon e instruments. El PUT crea o actualiza el perfil y reemplaza
 * las metas en un batch atómico.
 */

export const ONBOARDING_KEY = "/onboarding";

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

/** Cuerpo de `PUT /api/onboarding` a partir de los valores validados. */
export function toOnboardingBody(values: OnboardingValues) {
  return {
    risk_level: values.riskLevel,
    capital: values.capital,
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
export async function fetchProfile(): Promise<ProfileRow | null> {
  try {
    return (await apiFetch<ProfileRow | null>(ONBOARDING_KEY)) ?? null;
  } catch (err) {
    throw new OnboardingSaveError(`No se pudo leer tu perfil. ${apiErrorMessage(err)}`, { cause: err });
  }
}

/** Crea o actualiza el perfil (uno por usuario) y reemplaza sus metas. */
export async function saveOnboarding(values: OnboardingValues): Promise<{ profileId: string }> {
  try {
    const res = await apiFetch<{ profileId: string; profile_id: string }>(ONBOARDING_KEY, {
      method: "PUT",
      body: toOnboardingBody(values),
    });
    return { profileId: res.profileId ?? res.profile_id };
  } catch (err) {
    throw new OnboardingSaveError(`No se pudo guardar tu perfil. ${apiErrorMessage(err)}`, { cause: err });
  }
}
