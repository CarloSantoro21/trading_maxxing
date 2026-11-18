---
name: nueva-estrategia
description: Flujo completo para proponer, implementar y validar una estrategia nueva de trading en trading_maxxing. Úsalo cuando un usuario pida "agregar una estrategia", "proponer un BaseAgent", "implementar una idea del paper X" o similar. Cubre el criterio de evidencia publicada, el scaffolding de la subclase de BaseAgent, las pruebas mínimas, el paso por el laboratorio (walk-forward + Monte Carlo según `docs/estrategias/criterios-de-aprobacion.md`) y la apertura del PR con el reporte del laboratorio.
---

# Skill: nueva-estrategia

Una estrategia nueva en `trading_maxxing` es una subclase de `BaseAgent` (ver `services/agents/src/tm_agents/base.py`) registrada con `@register_strategy("nombre")` y colocada en `services/agents/src/tm_agents/strategies/`. Esta skill guía el camino desde "tengo una idea" hasta "el PR está abierto y el reporte del laboratorio adjunto".

> **Nota importante.** El ejemplo `services/agents/src/tm_agents/strategies/example_sma_cross.py` (con pruebas en `services/agents/tests/test_example_sma_cross.py`) **se produjo siguiendo esta skill**, como demostración de que el flujo da código que compila y pasa pruebas. Se queda en `state=research` en el registro (sin capital) y no está pensado para activarse en producción: existe solo como evidencia de que la plantilla funciona.

## Workflow

Sigue los pasos en orden. No saltes el paso 1: una estrategia sin evidencia publicada se rechaza en revisión de código.

### 1. Buscar evidencia publicada

Una estrategia nueva solo entra si cumple **al menos uno** de estos criterios de evidencia:

- **Revisión por pares.** Un artículo en una revista indexada (Crossref) que documente el efecto en cripto.
- **Confirmación out-of-sample.** El efecto se mantiene en datos posteriores a los del paper original (de preferencia 2020–2025 de Binance Spot, nuestros datos).

Antes de escribir código, deja escrito en el docstring de la estrategia:

1. **Cita completa** del paper (autores, año, revista, volumen, páginas y DOI). Igual que las entradas de `docs/estrategias/investigacion-nuevas-estrategias.md`.
2. **Por qué cripto en particular.** El paper debe medir el efecto en BTC o ETH (u otra cripto líquida). Las reglas pensadas para acciones **no cuentan** salvo que haya una confirmación cripto independiente.
3. **Regla operable en Binance Spot.** Solo largo o USDT; sin cortos, sin apalancamiento, sin futuros. Con comisión de 0.1 % por lado, las reglas intradía rara vez sobreviven: calcula el costo anual aproximado antes de seguir.
4. **Correlación esperada contra el `TrendAgent`.** Si la estrategia es muy parecida al trend, su valor agregado es bajo. Ver la sección 2 de `docs/estrategias/investigacion-nuevas-estrategias.md`.

Si ninguno de los puntos anteriores aplica, **detente aquí** y escribe en el issue por qué no procede.

### 2. Scaffold del `BaseAgent`

Copia `templates/strategy.py.tmpl` a `services/agents/src/tm_agents/strategies/<nombre>.py` y rellena los TODOs. Recuerda:

- La estrategia **no puede mirar el futuro**. Usa `closed_bars(ctx.bars_for(self.timeframe), now, period)` de `tm_agents.strategies.trend` para quedarte solo con velas ya cerradas en `ctx.now`.
- `analyze()` debe ser determinista: dados los mismos `AgentContext` y parámetros, devuelve el mismo `Signal`.
- Expón en `metadata` lo que el orquestador y el laboratorio necesitan (`bars_used`, `last_close`, lo que explique la decisión).
- Registra la clase con `@register_strategy("<nombre>")` y, al final, agrega la importación a `services/agents/src/tm_agents/strategies/__init__.py` para que el registro la vea.

Toma como referencia `strategies/trend.py` (canales Donchian) y `strategies/intraday_momentum.py` (reglas intradía con sesiones ET). El primero cubre bien el patrón "parámetros + regla sin look-ahead + metadata".

### 3. Pruebas

Copia `templates/test_strategy.py.tmpl` a `services/agents/tests/test_<nombre>.py` y rellena al menos las cuatro pruebas de la plantilla:

1. **Regla baseline.** Un caso sintético obvio (tendencia clara, cruz clara, etc.) donde la dirección y la confianza son conocidas.
2. **Sin look-ahead.** Modificar la vela aún abierta en `now` **no debe** cambiar la señal.
3. **Historia insuficiente.** Con muy pocas velas, la señal es `FLAT` (o 0 de confianza, según la estrategia).
4. **Metadata.** Las llaves documentadas existen y tienen el tipo esperado.

La cobertura mínima del proyecto es 70 % (ver `pyproject.toml` y `CONTRIBUTING.md`). Añade más pruebas si tu estrategia tiene ramas complejas (modos, parámetros de ventana, etc.).

Para correr solo tu archivo:

```bash
cd services/agents && uv run pytest tests/test_<nombre>.py -q
```

o, si no estás usando `uv`:

```bash
cd services/agents && python -m pytest tests/test_<nombre>.py -q
```

### 4. Laboratorio

Con la estrategia pasando sus pruebas unitarias, pásala por el laboratorio para medir si cumple los criterios de aprobación. Consulta `docs/estrategias/criterios-de-aprobacion.md` para los umbrales exactos. En resumen:

| Criterio | Herramienta | Umbral |
|---|---|---|
| Walk-forward (Sharpe out-of-sample) | `tm_agents.montecarlo.evaluate` sobre 20 tramos de 2021–2025 | Sharpe > 0.5 y > buy & hold |
| Drawdown p95 a 1 año | Monte Carlo de block bootstrap (horizonte 365 días) | < 40 % global |
| Suficientes trades | Conteo sobre el walk-forward | ≥ 100 trades, o ≥ 30 con 3 años de exposición diaria |

El laboratorio debe correr **sin tocar el holdout 2026**. Si la estrategia no pasa, documenta el porqué en el issue y vuelve al paso 2 (ajusta parámetros dentro del walk-forward, nunca mirando todo el histórico: eso es *data snooping*).

El resultado se registra en el `StrategyRegistry` con `record_lab_report`. La estrategia nace en `state=research`; después del laboratorio pasa a `state=lab`; después de 7 días en shadow y una aprobación humana entra a `state=active`.

### 5. PR

Cuando la estrategia pase las pruebas y el laboratorio tenga un reporte:

1. Crea la rama desde `develop`: `feature/<issue>-<nombre>-estrategia`.
2. Commit con [Conventional Commits](https://www.conventionalcommits.org/): `feat(agents): <nombre>Agent (#<issue>)`.
3. Abre el PR contra `develop` usando `.github/pull_request_template.md`. Incluye:
   - `Closes #<issue>` en la sección "Issue".
   - En "Cómo se probó", la línea de comando que ejecutaste y el reporte del laboratorio (Sharpe, drawdown p95, número de trades).
4. Verifica que el CI pasa (lint + pruebas + cobertura ≥ 70 %). Merge con squash.

## Dónde mirar

- Contrato de `BaseAgent`: `services/agents/src/tm_agents/base.py`.
- Referencia de estilo (daily): `services/agents/src/tm_agents/strategies/trend.py` y su test `tests/test_trend.py`.
- Referencia de estilo (intradía): `services/agents/src/tm_agents/strategies/intraday_momentum.py`.
- Criterios de aprobación: `docs/estrategias/criterios-de-aprobacion.md`.
- Investigación previa y filtros: `docs/estrategias/investigacion-nuevas-estrategias.md`.
- Ciclo de vida de estrategias: `services/agents/src/tm_agents/registry.py` y `strategy_stats.py`.

## Ejemplo ya implementado por esta skill

`services/agents/src/tm_agents/strategies/example_sma_cross.py` es un cruce de medias móviles trivial creado paso a paso siguiendo este flujo. Su propósito es ser un sanity check del scaffolding: existe, compila, pasa pruebas y se queda en `state=research` sin capital. **No lo habilites en producción.** Si quieres un sleeve basado en SMA, implementa el `MovingAverageAgent` de la sección 3.2 de `investigacion-nuevas-estrategias.md`, que tiene evidencia publicada.
