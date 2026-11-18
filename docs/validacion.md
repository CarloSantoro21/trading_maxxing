# Reporte de validación go/no-go (#44)

> **Cambio de plan (2026-11-17):** este reporte ya no condiciona el deploy a
> la nube. La rúbrica exige dev y prod sí o sí (ver `docs/rubrica.md`, #26,
> #27, #45). El go/no-go se queda solo como validación de estrategias y
> paper trading y **tiene menos prioridad que la ruta crítica del deploy**.

> **Plantilla.** Este documento se llena después de correr al menos 5 días de
> paper trading continuo contra Binance Spot Testnet. Hasta entonces los
> campos marcados como `TBD` quedan tal cual. Este PR solo deja listo el
> andamiaje (plantilla, checklist operativo y script de métricas); correr la
> ventana de prueba y escribir la decisión final forma parte del cierre
> definitivo de #44.
>
> La ventana de paper trading se puede correr **contra el entorno de Azure
> dev** en lugar de solo en la Mac local (ver la sección "Alternativa: correr
> contra Azure dev" en [`validacion-checklist.md`](validacion-checklist.md)).

## 1. Resumen ejecutivo

- **Decisión:** `TBD` (go / no-go / repetir).
- **Responsable de la decisión:** `TBD`.
- **Fecha de la decisión:** `TBD`.
- **Justificación en una línea:** `TBD`.

## 2. Entorno de prueba

| Campo | Valor |
|---|---|
| Host | Mac mini M-series con `caffeinate -dims` **o** entorno de Azure dev (ver checklist) |
| Mercado | Binance **Spot Testnet** (BTC/USDT, ETH/USDT) |
| Servicios en marcha | `api`, `agents`, `engine`, `forecast`, Supabase CLI, Redis (vía `make up`) |
| Fuente de verdad | Tabla `public.trades` + `public.equity_snapshots` de Supabase |
| Modelo de comisiones asumido en el reporte | 0.1 % por lado (Binance Spot) |
| Perfil de riesgo probado | `TBD` (nivel 1-5) |
| Capital inicial virtual | `TBD` USDT |

### Limitaciones declaradas

- **La testnet no refleja la liquidez ni el slippage del mercado real.** Los
  books son más delgados y los fills suelen ser demasiado optimistas. Toda
  decisión de go/no-go tiene que considerar explícitamente este sesgo.
- **La testnet se reinicia** sin aviso de Binance: cualquier hueco en los
  fills se anota en la sección de logs.
- **La latencia** medida contra la testnet no es representativa de la red
  productiva.

## 3. Periodo de prueba

| Campo | Valor |
|---|---|
| Inicio (UTC) | `TBD` |
| Fin (UTC) | `TBD` |
| Duración objetivo | ≥ 5 días calendario de ejecución continua |
| Días efectivos de operación | `TBD` |
| Reinicios programados | `TBD` |
| Caídas no programadas | `TBD` (ver sección 7) |
| Mantenimientos de Binance Testnet | `TBD` |

## 4. Estrategias activas

Cada fila enlaza al reporte del laboratorio en `docs/estrategias/reportes/`
(ya aprobado contra los criterios de #13 / `tm_agents.montecarlo`).

| Estrategia | Estado | Reporte OOS + Monte Carlo | Veredicto del laboratorio | Promovida por |
|---|---|---|---|---|
| Trend con volatility targeting | activa | [`trend_vol.json`](estrategias/reportes/trend_vol.json) | Sharpe 0.78 · DD p95 1 año 35.6 % · aprobada | `TBD` |
| Trend (Donchian) sin vol targeting | referencia | [`trend.json`](estrategias/reportes/trend.json) | DD p95 43.8 % > 40 %: rechazada | — |
| Momentum intradía | referencia | [`intraday_momentum.json`](estrategias/reportes/intraday_momentum.json) | Sharpe −2.65: rechazada | — |
| Otras en shadow | `TBD` | `TBD` | `TBD` | `TBD` |

> La lista final se completa con `tm_agents.lifecycle`: se copian las
> estrategias cuyo estado al inicio de la ventana es `active` o `shadow`.

## 5. Métricas de P&L

Se generan con `scripts/validate_paper_run.py` sobre el CSV de fills
exportado al final de la ventana. Todos los valores asumen 0.1 % de comisión
por lado (ya aplicados a las filas; el script toma `fees` cuando están).

| Métrica | Valor | Umbral referencia |
|---|---|---|
| P&L neto de comisiones (USDT) | `TBD` | > 0 para ir a *go* |
| Retorno neto sobre capital inicial | `TBD` | — |
| Sharpe diario | `TBD` | > 0.5 (criterio #1 del laboratorio) |
| Volatilidad diaria anualizada | `TBD` | — |
| Drawdown máximo observado | `TBD` | ≤ techo del perfil (40 % global) |
| Drawdown vs límite del perfil | `TBD` / `TBD` | ≤ 1 |
| Hit rate | `TBD` | — |
| Nº de operaciones cerradas | `TBD` | — |
| Nº de operaciones abiertas al cierre | `TBD` | — |
| Comisiones pagadas (USDT) | `TBD` | — |

Fragmento generado por el script (pegar la salida de `--format md`):

```markdown
<!-- pegar aquí la salida de scripts/validate_paper_run.py --format md -->
```

## 6. Curva de equity

- Imagen: `TBD` (ruta relativa, por ejemplo `docs/estrategias/img/validacion-equity.png`).
- CSV: `TBD` (por ejemplo `docs/estrategias/reportes/validacion-equity.csv`).
- Fuente: `public.equity_snapshots` filtrada por los bots activos durante la
  ventana.

## 7. Logs y reinicios

Tabla cronológica de todo lo que interrumpió la ejecución continua. Se llena
a mano desde `docker compose logs` / journalctl del host.

| Fecha y hora (UTC) | Componente | Tipo | Impacto | Notas |
|---|---|---|---|---|
| `TBD` | `TBD` (api/agents/engine/forecast/host) | reinicio/caída/mantenimiento | `TBD` | `TBD` |

Checklist de evidencias a archivar:

- [ ] `docker compose logs --since <inicio> > logs/validacion-<fecha>.log`
- [ ] Captura del dashboard al cierre con equity, DD y nº de operaciones.
- [ ] Export del CSV de fills usado para generar las métricas.

## 8. Decisión go/no-go

### Criterios

1. P&L neto ≥ 0 USDT al cierre de la ventana.
2. Drawdown observado ≤ techo del perfil activo.
3. Sharpe diario > 0 (idealmente > 0.5), consistente con lo visto en OOS.
4. Sin caídas no programadas > 30 minutos acumulados; motor sin posiciones
   fantasma al final.
5. Las estrategias activas al inicio de la ventana siguen activas al final
   (no se degradó ninguna por `tm_agents.lifecycle`).

### Resultado

- **Veredicto:** `TBD` (go / no-go / repetir).
- **Justificación:** `TBD`.
- **Sesgo de testnet considerado:** `TBD` (explicar cómo se ajustó la lectura
  dado que la liquidez y el slippage no son los del mercado real).
- **Acciones de seguimiento:** `TBD`.

---

**Referencias cruzadas**

- [Criterios de aprobación](estrategias/criterios-de-aprobacion.md)
- [Estado del proyecto](estado-del-proyecto.md)
- [Checklist operativo](validacion-checklist.md)
- Issue #44 (validación de estrategias y paper trading; **no bloquea deploy**
  a la nube: la rúbrica exige dev y prod sí o sí) y PR que introduce este
  scaffold.
