import Link from "next/link";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 p-8">
      <h1 className="text-3xl font-semibold">trading_maxxing</h1>
      <p className="text-neutral-600 dark:text-neutral-300">
        Plataforma multiestrategia de trading de criptomonedas. Opera solo en Binance Testnet.
      </p>
      <nav aria-label="Principal" className="flex flex-wrap gap-4 text-emerald-700 dark:text-emerald-400">
        <Link href="/login" className="underline underline-offset-4">
          Iniciar sesión
        </Link>
        <Link href="/dashboard" className="underline underline-offset-4">
          Dashboard
        </Link>
        <Link href="/strategies" className="underline underline-offset-4">
          Estrategias
        </Link>
        <Link href="/onboarding" className="underline underline-offset-4">
          Configurar perfil
        </Link>
      </nav>
    </main>
  );
}
