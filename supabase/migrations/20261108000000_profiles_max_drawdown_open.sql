-- profiles.max_drawdown pasa de (0, 1] a (0, 1).
--
-- El RiskAgent (RiskLimits.from_profile) exige 0 < max_drawdown < 1: con un drawdown máximo
-- de 100 % el kill switch no saltaría antes de perder todo el capital, así que la base ya no
-- lo acepta y queda alineada con el backend.

-- Un perfil que ya tuviera 1 (no hay ninguno en el seed) se ajusta al máximo válido de 99 %
-- para que el nuevo check se pueda crear.
update public.profiles set max_drawdown = 0.99 where max_drawdown >= 1;

alter table public.profiles drop constraint profiles_max_drawdown_range;

-- Fracción del capital estrictamente entre 0 y 1 (0.2 = 20 %).
alter table public.profiles
  add constraint profiles_max_drawdown_range check (max_drawdown > 0 and max_drawdown < 1);

comment on column public.profiles.max_drawdown is 'Drawdown máximo permitido como fracción en (0, 1) (0.2 = 20 %). Al rebasarlo, el kill switch detiene el bot.';
