# Checklist operativo del go/no-go (#44)

Pasos para correr la ventana de paper trading de al menos 5 días y llenar
[`validacion.md`](validacion.md). Está pensado para un operador humano en
una Mac con el stack local levantado por `make up`.

## 0. Preparación (día D-1)

- [ ] Rebase sobre `develop` y `make test && make lint` en verde.
- [ ] `.env` con llaves de Binance Spot Testnet (`BINANCE_API_KEY`,
      `BINANCE_API_SECRET`, `BINANCE_TESTNET=true`) y Supabase.
- [ ] Datos históricos presentes en `data/` (ver `estado-del-proyecto.md`).
- [ ] Bot de prueba creado con `make user EMAIL=... ROLE=member` y perfil de
      riesgo asignado (anotar el nivel en el reporte).
- [ ] Bitácora abierta: `docs/validacion.md` con la columna de "Entorno de
      prueba" llenada (host, perfil, capital inicial).

## 1. Arranque (día D)

- [ ] `make up` (Supabase CLI + Redis + api + agents + engine + forecast).
- [ ] `make web` para tener el dashboard visible.
- [ ] En otra terminal: `caffeinate -dims` (que no suspenda la Mac).
- [ ] Verificar en el dashboard que los bots quedaron en `active` y sin
      posiciones fantasma.
- [ ] Anotar la hora UTC de inicio en la sección 3 de `validacion.md`.

## 2. Monitoreo diario

Cada 12 horas aproximadamente:

- [ ] `docker compose ps` (todos `Up`).
- [ ] Revisar `docker compose logs --since 12h engine agents api` en busca
      de excepciones, reconexiones a Binance o fills raros.
- [ ] Capturar del dashboard: equity, drawdown y nº de operaciones abiertas.
- [ ] Anotar cualquier reinicio o caída en la tabla cronológica (sección 7).

## 3. Cierre (día D+5 o más)

- [ ] Anotar hora UTC de fin.
- [ ] Exportar los fills de la ventana:

  ```sql
  copy (
    select
      t.closed_at as ts,
      t.symbol,
      t.side,
      t.quantity,
      t.entry_price,
      t.exit_price,
      t.fees,
      t.pnl
    from public.trades t
    where t.closed_at between '<inicio>' and '<fin>'
    order by t.closed_at
  ) to stdout with csv header
  ```

  Guardar la salida en `docs/estrategias/reportes/validacion-fills.csv`.

- [ ] Exportar la curva de equity de `public.equity_snapshots` a
      `docs/estrategias/reportes/validacion-equity.csv`.
- [ ] Capturar la gráfica del dashboard a
      `docs/estrategias/img/validacion-equity.png`.

## 4. Métricas y reporte

- [ ] Correr el script de métricas:

  ```bash
  uv run python scripts/validate_paper_run.py \
    --fills docs/estrategias/reportes/validacion-fills.csv \
    --format md > /tmp/metricas.md
  ```

- [ ] Pegar el fragmento en la sección 5 de `validacion.md`.
- [ ] Llenar la sección 4 (estrategias activas al cierre) consultando el
      estado real desde `tm_agents.lifecycle` o la tabla `strategies`.
- [ ] Llenar las limitaciones observadas (slippage anómalo, cortes de
      testnet, etc.) en la sección 2.
- [ ] Guardar `docker compose logs` completos en `logs/validacion-<fecha>.log`.

## 5. Decisión

- [ ] Evaluar los 5 criterios de la sección 8 de `validacion.md`.
- [ ] Escribir veredicto + justificación corta.
- [ ] Mencionar explícitamente el sesgo de la testnet (liquidez / slippage).
- [ ] Commit, PR aparte con el reporte final, cierre de #44.

## Qué NO hacer

- No ajustar parámetros de las estrategias durante la ventana. Cualquier
  cambio invalida la medición y hay que reiniciar los 5 días.
- No mover fondos del bot entre niveles de riesgo durante la prueba.
- No borrar fills ni snapshots de equity para "limpiar" la curva.
