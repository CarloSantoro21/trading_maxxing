-- Tipos enum del dominio de trading_maxxing.
-- Los valores coinciden con los contratos de Python (packages/shared); si cambian aquí,
-- deben cambiar allá también.

-- Horizonte de inversión del perfil: corto, mediano o largo plazo.
create type public.horizon as enum ('short', 'medium', 'long');

-- Dirección de una señal o de una posición. 'flat' significa sin posición.
-- Lo usan las señales dentro de trades.rationale y el backend; se declara aquí
-- para que el tipo exista en la base y en los tipos generados del frontend.
create type public.direction as enum ('long', 'short', 'flat');

-- Lado de la orden de entrada de una operación.
create type public.order_side as enum ('buy', 'sell');

-- Estado de un bot. 'killed' lo pone el kill switch al rebasar el drawdown máximo.
create type public.bot_status as enum ('stopped', 'running', 'paused', 'killed');

-- Estado de un backtest en la cola de jobs.
create type public.backtest_status as enum ('queued', 'running', 'done', 'failed');
