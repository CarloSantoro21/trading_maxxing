# Checklist operativo del go/no-go (#44)

> **Cambio de plan (2026-11-17):** este checklist ya no bloquea el deploy a
> la nube. La rúbrica exige dev y prod sí o sí (ver `docs/rubrica.md`, #26,
> #27, #45). El go/no-go se queda como validación de estrategias y paper
> trading, con **menos prioridad que la ruta crítica del deploy**.

Pasos para correr la ventana de paper trading de al menos 5 días y llenar
[`validacion.md`](validacion.md). Está pensado para un operador humano en
una Mac con el stack local levantado por `make up`. En la sección
["Alternativa: correr contra Azure dev"](#alternativa-correr-contra-azure-dev)
se documenta la opción de hacerlo contra el entorno de dev en la nube.

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

## Alternativa: correr contra Azure dev

Dado que ya existe deploy a la nube (dev y prod) por la rúbrica (#26, #27,
#45), la ventana de paper trading se puede ejecutar **contra el entorno de
Azure dev** en vez de la Mac local. Beneficios: no depende de que la Mac
siga encendida, el stack ya está armado idéntico al de prod (mismo
contenedor, misma base), y los logs quedan centralizados en Azure.

Diferencias respecto al flujo "desde la Mac":

- No hace falta `make up` ni `caffeinate -dims`; el stack ya corre en Azure
  dev con los mismos servicios (`api`, `agents`, `engine`, `forecast`).
- Las llaves de Binance Spot Testnet se cargan como variables de entorno /
  secretos del deployment en Azure dev, **no** en el `.env` local.
- La fuente de verdad para los fills sigue siendo `public.trades` y
  `public.equity_snapshots` en Supabase (no cambia el export).
- `docker compose logs` se reemplaza por los logs del servicio en Azure
  (ver `docs/arquitectura/` y el panel de la suscripción). Guardar la
  misma evidencia en `logs/validacion-<fecha>.log`.
- El monitoreo cada 12 h se hace contra el dashboard desplegado (URL de
  dev), no contra `make web`.
- El cierre y la generación de métricas con `scripts/validate_paper_run.py`
  no cambian: siguen siendo sobre el CSV exportado de Supabase.

Si se corre contra Azure dev, anotarlo explícitamente en la sección 2
("Entorno de prueba") de `validacion.md` en lugar del host de la Mac.

## Qué NO hacer

- No ajustar parámetros de las estrategias durante la ventana. Cualquier
  cambio invalida la medición y hay que reiniciar los 5 días.
- No mover fondos del bot entre niveles de riesgo durante la prueba.
- No borrar fills ni snapshots de equity para "limpiar" la curva.
