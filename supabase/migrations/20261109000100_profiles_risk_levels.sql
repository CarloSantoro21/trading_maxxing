-- Onboarding simplificado (issue #78, propuesta sección 3.4).
--
-- El usuario solo elige capital, nivel de riesgo (1 a 5) y metas. Por eso:
--   * profiles.max_drawdown deja de capturarse: lo deriva un trigger del risk_level con la
--     tabla única de tm_agents.risk.RISK_TABLE (10, 15, 20, 30 y 40 %).
--   * horizon e instruments tienen valores por defecto (medium; BTCUSDT y ETHUSDT), así que
--     el onboarding ya no los manda. Se opera siempre BTCUSDT y ETHUSDT.
--   * El capital mínimo es de 500 USDT de testnet.

-- Drawdown máximo de cada nivel. Debe coincidir con tm_agents.risk.RISK_TABLE
-- (services/agents/tests/test_risk_levels_sync.py lo verifica). Fuera de 1..5 da null.
create function private.risk_level_max_drawdown(level smallint)
returns numeric
language sql
immutable
set search_path = ''
as $$
  select case level
    when 1 then 0.10
    when 2 then 0.15
    when 3 then 0.20
    when 4 then 0.30
    when 5 then 0.40
  end;
$$;

-- Antes de cada INSERT o UPDATE, max_drawdown se recalcula a partir de risk_level; lo que
-- venga en la fila para max_drawdown se ignora.
create function private.derive_profile_max_drawdown()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.risk_level is null or new.risk_level not between 1 and 5 then
    -- Mismo código que el check profiles_risk_level_range, que no alcanzaría a correr
    -- porque antes fallaría el not null de max_drawdown.
    raise exception 'risk_level debe estar entre 1 y 5 (recibido: %)', new.risk_level
      using errcode = '23514';
  end if;
  new.max_drawdown := private.risk_level_max_drawdown(new.risk_level);
  return new;
end;
$$;

create trigger profiles_derive_max_drawdown before insert or update on public.profiles
  for each row execute function private.derive_profile_max_drawdown();

-- Perfiles existentes: el UPDATE dispara el trigger y alinea su max_drawdown con el nivel.
-- Un capital menor a 500 (no hay ninguno en el seed) se sube al mínimo para crear el check.
update public.profiles set capital = greatest(capital, 500);

alter table public.profiles drop constraint profiles_capital_positive;
alter table public.profiles
  add constraint profiles_capital_min check (capital >= 500);

alter table public.profiles alter column horizon set default 'medium';
alter table public.profiles
  alter column instruments set default array['BTCUSDT', 'ETHUSDT']::text[];

comment on column public.profiles.max_drawdown is 'Drawdown máximo como fracción, derivado de risk_level por el trigger profiles_derive_max_drawdown (1: 0.10, 2: 0.15, 3: 0.20, 4: 0.30, 5: 0.40). Al rebasarlo, el kill switch detiene el bot.';
comment on column public.profiles.capital is 'Capital en USDT de testnet; mínimo 500.';
comment on column public.profiles.horizon is 'Ya no se captura en el onboarding; por defecto medium.';
comment on column public.profiles.instruments is 'Ya no se captura en el onboarding; siempre BTCUSDT y ETHUSDT por defecto.';
