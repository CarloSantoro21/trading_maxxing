"use client";

import type { SupabaseClient } from "@supabase/supabase-js";
import { useEffect, useId, useRef, useState } from "react";
import {
  cardClass,
  errorClass,
  helpClass,
  inputClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "@/components/ui/styles";
import { OnboardingSaveError, saveOnboarding } from "@/lib/onboarding/repository";
import {
  EMPTY_FORM,
  errorsForStep,
  HORIZON_LABELS,
  HORIZONS,
  MAX_GOALS,
  RISK_LABELS,
  RISK_LEVELS,
  STEPS,
  SYMBOL_LABELS,
  SYMBOLS,
  todayIso,
  validateOnboarding,
  type FieldErrors,
  type GoalFormState,
  type OnboardingFormState,
  type SupportedSymbol,
} from "@/lib/onboarding/schema";

const HORIZON_HELP: Record<string, string> = {
  short: "Días a semanas. Estrategias más activas.",
  medium: "Semanas a meses.",
  long: "Meses o más. Menos operaciones.",
};

export function OnboardingWizard({
  supabase,
  userId,
  initial = EMPTY_FORM,
  onSaved,
}: {
  supabase: SupabaseClient;
  userId: string;
  initial?: OnboardingFormState;
  onSaved?: (profileId: string) => void;
}) {
  const [form, setForm] = useState<OnboardingFormState>(initial);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);
  const id = useId();

  // Al cambiar de paso, el foco va al título para lectores de pantalla.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  function update<K extends keyof OnboardingFormState>(key: K, value: OnboardingFormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function updateGoal(index: number, patch: Partial<GoalFormState>) {
    setForm((f) => ({
      ...f,
      goals: f.goals.map((g, i) => (i === index ? { ...g, ...patch } : g)),
    }));
  }

  function toggleInstrument(symbol: SupportedSymbol, checked: boolean) {
    setForm((f) => ({
      ...f,
      instruments: checked
        ? SYMBOLS.filter((s) => s === symbol || f.instruments.includes(s))
        : f.instruments.filter((s) => s !== symbol),
    }));
  }

  function handleNext() {
    const result = validateOnboarding(form);
    const stepErrors = result.success ? {} : errorsForStep(result.errors, step);
    setErrors(stepErrors);
    if (Object.keys(stepErrors).length === 0) setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  function handleBack() {
    setErrors({});
    setStep((s) => Math.max(s - 1, 0));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step < STEPS.length - 1) {
      handleNext();
      return;
    }
    const result = validateOnboarding(form);
    if (!result.success) {
      // Lleva al primer paso con errores.
      const firstBad = STEPS.findIndex((_, i) => Object.keys(errorsForStep(result.errors, i)).length);
      setStep(firstBad === -1 ? step : firstBad);
      setErrors(result.errors);
      return;
    }
    setErrors({});
    setSaveError(null);
    setSaving(true);
    try {
      const { profileId } = await saveOnboarding(supabase, userId, result.data);
      setSaved(true);
      onSaved?.(profileId);
    } catch (err) {
      setSaveError(
        err instanceof OnboardingSaveError ? err.message : "Ocurrió un error inesperado al guardar.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (saved) {
    return (
      <section className={cardClass} aria-labelledby={`${id}-saved`}>
        <h2 id={`${id}-saved`} className="text-xl font-semibold">
          ¡Perfil guardado!
        </h2>
        <p className="mt-2 text-neutral-600 dark:text-neutral-300">
          Tu perfil de inversión y tus metas quedaron registrados. Puedes volver a esta página
          cuando quieras para cambiarlos.
        </p>
        <button type="button" className={`${secondaryButtonClass} mt-4`} onClick={() => setSaved(false)}>
          Editar de nuevo
        </button>
      </section>
    );
  }

  const fieldError = (key: string) =>
    errors[key] ? (
      <p id={`${id}-${key}-error`} className={errorClass}>
        {errors[key]}
      </p>
    ) : null;
  const describedBy = (key: string, help?: string) =>
    [errors[key] ? `${id}-${key}-error` : null, help ?? null].filter(Boolean).join(" ") || undefined;

  const current = STEPS[step];

  return (
    <div className="space-y-6">
      <nav aria-label="Progreso del onboarding">
        <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {STEPS.map((s, i) => (
            <li
              key={s.id}
              aria-current={i === step ? "step" : undefined}
              className={`rounded-md border px-3 py-2 text-sm ${
                i === step
                  ? "border-emerald-700 bg-emerald-50 font-semibold text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
                  : i < step
                    ? "border-neutral-300 text-neutral-700 dark:border-neutral-700 dark:text-neutral-300"
                    : "border-neutral-200 text-neutral-500 dark:border-neutral-800"
              }`}
            >
              <span className="block text-xs font-normal">Paso {i + 1}</span>
              {s.title}
            </li>
          ))}
        </ol>
      </nav>

      <form noValidate onSubmit={handleSubmit} className={cardClass} aria-labelledby={`${id}-title`}>
        <h2
          id={`${id}-title`}
          ref={headingRef}
          tabIndex={-1}
          className="text-xl font-semibold focus:outline-none"
        >
          {current.title}
        </h2>

        <div className="mt-6 space-y-6">
          {current.id === "capital" && (
            <>
              <div>
                <label htmlFor={`${id}-capital`} className={labelClass}>
                  Capital inicial (USDT)
                </label>
                <input
                  id={`${id}-capital`}
                  name="capital"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  step="any"
                  value={form.capital}
                  onChange={(e) => update("capital", e.target.value)}
                  aria-invalid={errors.capital ? true : undefined}
                  aria-describedby={describedBy("capital", `${id}-capital-help`)}
                  className={`${inputClass} mt-1`}
                />
                <p id={`${id}-capital-help`} className={helpClass}>
                  Monto de la cuenta de Binance Testnet que operará la plataforma.
                </p>
                {fieldError("capital")}
              </div>

              <fieldset aria-describedby={describedBy("horizon")}>
                <legend className={labelClass}>Horizonte de inversión</legend>
                <div className="mt-2 grid gap-2 sm:grid-cols-3">
                  {HORIZONS.map((h) => (
                    <label
                      key={h}
                      className="flex cursor-pointer gap-2 rounded-md border border-neutral-300 p-3 has-[:checked]:border-emerald-700 has-[:checked]:bg-emerald-50 dark:border-neutral-700 dark:has-[:checked]:bg-emerald-950"
                    >
                      <input
                        type="radio"
                        name="horizon"
                        value={h}
                        checked={form.horizon === h}
                        onChange={() => update("horizon", h)}
                        className="mt-1 accent-emerald-700"
                      />
                      <span>
                        <span className="block font-medium">{HORIZON_LABELS[h]}</span>
                        <span className="block text-xs text-neutral-500">{HORIZON_HELP[h]}</span>
                      </span>
                    </label>
                  ))}
                </div>
                {fieldError("horizon")}
              </fieldset>
            </>
          )}

          {current.id === "risk" && (
            <>
              <fieldset aria-describedby={describedBy("riskLevel")}>
                <legend className={labelClass}>Nivel de riesgo (1 a 5)</legend>
                <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-5">
                  {RISK_LEVELS.map((level) => (
                    <label
                      key={level}
                      className="flex cursor-pointer items-center gap-2 rounded-md border border-neutral-300 p-3 has-[:checked]:border-emerald-700 has-[:checked]:bg-emerald-50 sm:flex-col sm:text-center dark:border-neutral-700 dark:has-[:checked]:bg-emerald-950"
                    >
                      <input
                        type="radio"
                        name="riskLevel"
                        value={level}
                        checked={form.riskLevel === String(level)}
                        onChange={() => update("riskLevel", String(level))}
                        className="accent-emerald-700"
                      />
                      <span className="text-sm">
                        <span className="font-semibold">{level}</span> · {RISK_LABELS[level]}
                      </span>
                    </label>
                  ))}
                </div>
                {fieldError("riskLevel")}
              </fieldset>

              <div>
                <label htmlFor={`${id}-dd`} className={labelClass}>
                  Drawdown máximo (%)
                </label>
                <input
                  id={`${id}-dd`}
                  name="maxDrawdownPct"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  max={100}
                  step="any"
                  value={form.maxDrawdownPct}
                  onChange={(e) => update("maxDrawdownPct", e.target.value)}
                  aria-invalid={errors.maxDrawdownPct ? true : undefined}
                  aria-describedby={describedBy("maxDrawdownPct", `${id}-dd-help`)}
                  className={`${inputClass} mt-1`}
                />
                <p id={`${id}-dd-help`} className={helpClass}>
                  Si la cuenta cae este porcentaje desde su máximo, el kill switch detiene el bot.
                </p>
                {fieldError("maxDrawdownPct")}
              </div>
            </>
          )}

          {current.id === "instruments" && (
            <fieldset aria-describedby={describedBy("instruments", `${id}-inst-help`)}>
              <legend className={labelClass}>Instrumentos a operar</legend>
              <p id={`${id}-inst-help`} className={helpClass}>
                Solo spot: se compra o se queda en USDT, nunca en corto.
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {SYMBOLS.map((s) => (
                  <label
                    key={s}
                    className="flex cursor-pointer items-center gap-2 rounded-md border border-neutral-300 p-3 has-[:checked]:border-emerald-700 has-[:checked]:bg-emerald-50 dark:border-neutral-700 dark:has-[:checked]:bg-emerald-950"
                  >
                    <input
                      type="checkbox"
                      name="instruments"
                      value={s}
                      checked={form.instruments.includes(s)}
                      onChange={(e) => toggleInstrument(s, e.target.checked)}
                      className="accent-emerald-700"
                    />
                    <span className="font-medium">{SYMBOL_LABELS[s]}</span>
                  </label>
                ))}
              </div>
              {fieldError("instruments")}
            </fieldset>
          )}

          {current.id === "goals" && (
            <>
              <ul className="space-y-4" aria-label="Metas">
                {form.goals.map((goal, index) => (
                  <li
                    key={index}
                    className="grid gap-3 rounded-md border border-neutral-200 p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-start dark:border-neutral-800"
                  >
                    <div>
                      <label htmlFor={`${id}-goal-${index}-ret`} className={labelClass}>
                        Retorno objetivo (%) · meta {index + 1}
                      </label>
                      <input
                        id={`${id}-goal-${index}-ret`}
                        type="number"
                        inputMode="decimal"
                        min={0}
                        step="any"
                        value={goal.targetReturnPct}
                        onChange={(e) => updateGoal(index, { targetReturnPct: e.target.value })}
                        aria-invalid={errors[`goals.${index}.targetReturnPct`] ? true : undefined}
                        aria-describedby={describedBy(`goals.${index}.targetReturnPct`)}
                        className={`${inputClass} mt-1`}
                      />
                      {fieldError(`goals.${index}.targetReturnPct`)}
                    </div>
                    <div>
                      <label htmlFor={`${id}-goal-${index}-date`} className={labelClass}>
                        Fecha límite · meta {index + 1}
                      </label>
                      <input
                        id={`${id}-goal-${index}-date`}
                        type="date"
                        min={todayIso()}
                        value={goal.deadline}
                        onChange={(e) => updateGoal(index, { deadline: e.target.value })}
                        aria-invalid={errors[`goals.${index}.deadline`] ? true : undefined}
                        aria-describedby={describedBy(`goals.${index}.deadline`)}
                        className={`${inputClass} mt-1`}
                      />
                      {fieldError(`goals.${index}.deadline`)}
                    </div>
                    {form.goals.length > 1 && (
                      <button
                        type="button"
                        className={`${secondaryButtonClass} sm:mt-6`}
                        onClick={() =>
                          update(
                            "goals",
                            form.goals.filter((_, i) => i !== index),
                          )
                        }
                      >
                        Quitar meta {index + 1}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              {fieldError("goals")}
              {form.goals.length < MAX_GOALS && (
                <button
                  type="button"
                  className={secondaryButtonClass}
                  onClick={() => update("goals", [...form.goals, { targetReturnPct: "", deadline: "" }])}
                >
                  Agregar meta
                </button>
              )}

              <section aria-labelledby={`${id}-summary`} className="rounded-md bg-neutral-50 p-4 dark:bg-neutral-900">
                <h3 id={`${id}-summary`} className="text-sm font-semibold">
                  Resumen
                </h3>
                <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <dt className="text-neutral-500">Capital</dt>
                  <dd>{form.capital ? `${form.capital} USDT` : "—"}</dd>
                  <dt className="text-neutral-500">Horizonte</dt>
                  <dd>{form.horizon ? HORIZON_LABELS[form.horizon] : "—"}</dd>
                  <dt className="text-neutral-500">Riesgo</dt>
                  <dd>
                    {form.riskLevel} · {RISK_LABELS[Number(form.riskLevel)] ?? "—"}
                  </dd>
                  <dt className="text-neutral-500">Drawdown máximo</dt>
                  <dd>{form.maxDrawdownPct ? `${form.maxDrawdownPct} %` : "—"}</dd>
                  <dt className="text-neutral-500">Instrumentos</dt>
                  <dd>{form.instruments.map((s) => SYMBOL_LABELS[s]).join(", ") || "—"}</dd>
                </dl>
              </section>
            </>
          )}
        </div>

        {saveError && (
          <div
            role="alert"
            className="mt-6 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
          >
            {saveError}
          </div>
        )}

        <div className="mt-8 flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
          <button
            type="button"
            className={secondaryButtonClass}
            onClick={handleBack}
            disabled={step === 0 || saving}
          >
            Atrás
          </button>
          {step < STEPS.length - 1 ? (
            <button type="submit" className={primaryButtonClass}>
              Siguiente
            </button>
          ) : (
            <button type="submit" className={primaryButtonClass} disabled={saving}>
              {saving ? "Guardando…" : "Guardar perfil"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
