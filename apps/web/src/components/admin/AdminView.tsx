"use client";

import { useId, useState } from "react";
import useSWR from "swr";
import { useMe } from "@/components/auth/RequireAuth";
import { Card, ConfirmPanel, EmptyState, ErrorBox, Loading } from "@/components/ui/feedback";
import { errorClass, helpClass, inputClass, labelClass, primaryButtonClass, secondaryButtonClass } from "@/components/ui/styles";
import {
  apiErrorMessage,
  createUser,
  deleteUser,
  triggerDemoError,
  updateSettings,
  updateUserRole,
  type AdminUser,
  type Role,
  type Settings,
} from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";

export const ADMIN_USERS_KEY = "/admin/users";
export const ADMIN_SETTINGS_KEY = "/admin/settings";

const ROLE_LABEL: Record<Role, string> = { admin: "Admin", member: "Miembro" };

const SETTINGS: { key: keyof Settings; label: string; help: string }[] = [
  {
    key: "news_enabled",
    label: "Noticias",
    help: "El motor lee noticias y las usa como señal. Apagado, opera solo con precios.",
  },
  {
    key: "explainer_enabled",
    label: "Explicaciones con LLM",
    help: "El motor redacta con un LLM la explicación de cada operación. Apagado, usa el texto estándar.",
  },
];

function NewUserForm({ onCreated }: { onCreated: () => void }) {
  const id = useId();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value) {
      setError("Escribe el correo.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const user = await createUser(value, role);
      setNotice(`${user.email} quedó dado de alta como ${ROLE_LABEL[user.role].toLowerCase()}.`);
      setEmail("");
      setRole("member");
      onCreated();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate aria-label="Dar de alta a un usuario" className="mt-6 space-y-3">
      <h3 className="text-sm font-semibold">Dar de alta</h3>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[16rem] flex-1">
          <label htmlFor={`${id}-email`} className={labelClass}>
            Correo
          </label>
          <input
            id={`${id}-email`}
            type="email"
            autoComplete="off"
            className={`${inputClass} mt-1`}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </div>
        <div>
          <label htmlFor={`${id}-role`} className={labelClass}>
            Rol
          </label>
          <select id={`${id}-role`} className={`${inputClass} mt-1`} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="member">{ROLE_LABEL.member}</option>
            <option value="admin">{ROLE_LABEL.admin}</option>
          </select>
        </div>
        <button type="submit" className={primaryButtonClass} disabled={busy}>
          {busy ? "Guardando…" : "Dar de alta"}
        </button>
      </div>
      <p className={helpClass}>
        Además hay que agregar el correo a la allowlist de Cloudflare Access (Terraform de la infraestructura); sin eso
        Access no le manda el código para entrar.
      </p>
      {error && (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
          {notice}
        </p>
      )}
    </form>
  );
}

function UserRow({ user, isSelf, onChanged }: { user: AdminUser; isSelf: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      setConfirmDelete(false);
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr className="border-t border-neutral-100 align-top dark:border-neutral-800">
      <th scope="row" className="py-2 pr-2 text-left font-normal">
        {user.email}
        {isSelf && <span className="ml-2 text-neutral-500">(tú)</span>}
        <span className="block text-xs text-neutral-500">alta: {formatDateTime(user.created_at)}</span>
      </th>
      <td className="py-2 pr-2">
        <label className="sr-only" htmlFor={`role-${user.id}`}>
          Rol de {user.email}
        </label>
        <select
          id={`role-${user.id}`}
          className={inputClass}
          value={user.role}
          disabled={busy || isSelf}
          onChange={(e) => void run(() => updateUserRole(user.id, e.target.value as Role))}
        >
          <option value="member">{ROLE_LABEL.member}</option>
          <option value="admin">{ROLE_LABEL.admin}</option>
        </select>
      </td>
      <td className="py-2">
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={busy || isSelf || confirmDelete}
          onClick={() => setConfirmDelete(true)}
          aria-label={`Dar de baja a ${user.email}`}
        >
          Dar de baja
        </button>
        {error && !confirmDelete && (
          <p role="alert" className={errorClass}>
            {error}
          </p>
        )}
        {confirmDelete && (
          <ConfirmPanel
            title={`¿Dar de baja a ${user.email}?`}
            description="Se borran su perfil, sus metas, sus bots y sus backtests. Quítalo también de la allowlist de Cloudflare Access."
            confirmLabel="Sí, dar de baja"
            tone="danger"
            busy={busy}
            error={error}
            onConfirm={() => void run(() => deleteUser(user.id))}
            onCancel={() => {
              setConfirmDelete(false);
              setError(null);
            }}
          />
        )}
      </td>
    </tr>
  );
}

function UsersCard() {
  const { me } = useMe();
  const { data, error, isLoading, mutate } = useSWR<AdminUser[]>(ADMIN_USERS_KEY);
  const reload = () => void mutate();
  return (
    <Card title="Usuarios">
      {isLoading && <Loading>Cargando usuarios…</Loading>}
      {error && <ErrorBox onRetry={reload}>{apiErrorMessage(error)}</ErrorBox>}
      {data && data.length === 0 && <EmptyState title="No hay usuarios" />}
      {data && data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-sm">
            <caption className="sr-only">Usuarios dados de alta</caption>
            <thead>
              <tr className="text-left text-neutral-500">
                <th scope="col" className="py-1 pr-2 font-medium">Correo</th>
                <th scope="col" className="py-1 pr-2 font-medium">Rol</th>
                <th scope="col" className="py-1 font-medium">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {data.map((u) => (
                <UserRow key={u.id} user={u} isSelf={u.id === me.id} onChanged={reload} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <NewUserForm onCreated={reload} />
    </Card>
  );
}

function SettingsCard() {
  const { data, error, isLoading, mutate } = useSWR<Settings>(ADMIN_SETTINGS_KEY);
  const [busy, setBusy] = useState<keyof Settings | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function toggle(key: keyof Settings) {
    if (!data) return;
    setBusy(key);
    setSaveError(null);
    try {
      const next = await updateSettings({ [key]: !data[key] });
      await mutate(next, { revalidate: false });
    } catch (err) {
      setSaveError(apiErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Ajustes del motor">
      {isLoading && <Loading>Cargando ajustes…</Loading>}
      {error && <ErrorBox onRetry={() => void mutate()}>{apiErrorMessage(error)}</ErrorBox>}
      {data && (
        <ul className="space-y-4">
          {SETTINGS.map((s) => {
            const on = data[s.key] === true;
            return (
              <li key={s.key} className="flex items-start justify-between gap-4">
                <div>
                  <p className="font-medium" id={`setting-${s.key}`}>
                    {s.label}
                  </p>
                  <p className={helpClass}>{s.help}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-labelledby={`setting-${s.key}`}
                  disabled={busy !== null}
                  onClick={() => void toggle(s.key)}
                  className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:opacity-60 ${
                    on ? "bg-emerald-600" : "bg-neutral-300 dark:bg-neutral-700"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`inline-block h-5 w-5 rounded-full bg-white shadow transition ${on ? "translate-x-5" : "translate-x-0.5"}`}
                  />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {saveError && (
        <p role="alert" className={errorClass}>
          {saveError}
        </p>
      )}
      <p className={`${helpClass} mt-4`}>El motor de la Mac lee estos ajustes en cada ciclo (GET /internal/settings).</p>
    </Card>
  );
}

function ApmDemoCard() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const requestId = await triggerDemoError();
      setResult(
        requestId
          ? `La API respondió 500 con request_id ${requestId}. Búscalo en Workers Observability.`
          : "La API respondió sin error (¿está desplegada la ruta de demo?).",
      );
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Demo de APM">
      <p className="text-sm text-neutral-600 dark:text-neutral-300">
        Provoca un error 500 controlado en el Worker: deja un log de error y una excepción con stack en Workers
        Observability.
      </p>
      <button type="button" className={`${secondaryButtonClass} mt-3`} disabled={busy} onClick={() => void handleClick()}>
        {busy ? "Provocando…" : "Provocar error de prueba"}
      </button>
      {result && (
        <p role="status" className="mt-2 text-sm">
          {result}
        </p>
      )}
      {error && (
        <p role="alert" className={errorClass}>
          {error}
        </p>
      )}
    </Card>
  );
}

/** Página /admin: usuarios (alta, baja y rol), interruptores del motor y la demo de APM. */
export function AdminView() {
  const { isAdmin } = useMe();
  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">Administración</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">
          Quién puede entrar a trading_maxxing y qué partes opcionales del motor están encendidas.
        </p>
      </div>
      {!isAdmin ? (
        <EmptyState title="Solo para admins">Pide a un admin que te cambie el rol si necesitas esta página.</EmptyState>
      ) : (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <UsersCard />
          </div>
          <div className="space-y-6">
            <SettingsCard />
            <ApmDemoCard />
          </div>
        </div>
      )}
    </>
  );
}
