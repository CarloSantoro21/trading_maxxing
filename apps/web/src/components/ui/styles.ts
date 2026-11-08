// Clases de Tailwind compartidas por los formularios (sin shadcn/ui para no agregar
// dependencias; los estilos siguen la misma idea: bordes suaves y foco visible).

export const inputClass =
  "block w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-base text-neutral-900 " +
  "shadow-sm placeholder:text-neutral-400 focus:border-emerald-600 focus:outline-none focus:ring-2 " +
  "focus:ring-emerald-600/30 aria-[invalid=true]:border-red-600 dark:border-neutral-700 " +
  "dark:bg-neutral-900 dark:text-neutral-100";

export const labelClass = "block text-sm font-medium text-neutral-800 dark:text-neutral-200";

export const errorClass = "mt-1 text-sm text-red-700 dark:text-red-400";

export const helpClass = "mt-1 text-sm text-neutral-500 dark:text-neutral-400";

export const primaryButtonClass =
  "inline-flex items-center justify-center rounded-md bg-emerald-700 px-4 py-2 text-sm font-semibold " +
  "text-white shadow-sm hover:bg-emerald-800 focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:cursor-not-allowed " +
  "disabled:opacity-60";

export const secondaryButtonClass =
  "inline-flex items-center justify-center rounded-md border border-neutral-300 px-4 py-2 text-sm " +
  "font-semibold text-neutral-800 hover:bg-neutral-100 focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-2 focus-visible:outline-emerald-700 disabled:cursor-not-allowed " +
  "disabled:opacity-60 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800";

export const cardClass =
  "rounded-xl border border-neutral-200 bg-white p-6 shadow-sm dark:border-neutral-800 dark:bg-neutral-950";
