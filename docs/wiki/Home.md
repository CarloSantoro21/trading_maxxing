# trading_maxxing — Propuesta de proyecto final

> Esta Wiki es la versión navegable de la propuesta. La fuente de verdad vive en el
> repositorio (`docs/wiki/`) y se sincroniza con `scripts/publish_wiki.sh`. Para el PDF
> original ve [`docs/propuesta/Propuesta-trading_maxxing.pdf`](https://github.com/CarloSantoro21/trading_maxxing/blob/main/docs/propuesta/Propuesta-trading_maxxing.pdf).

Infraestructura para el Desarrollo Continuo · ITESO · Otoño 2026

- Repositorio: https://github.com/CarloSantoro21/trading_maxxing
- Project Board: https://github.com/users/CarloSantoro21/projects/2
- Estado del proyecto: [`docs/estado-del-proyecto.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/main/docs/estado-del-proyecto.md)

Versión 2 (7 de noviembre de 2026). Cambios contra la versión 1: se agregan una biblioteca de estrategias con evidencia publicada en cripto, una fase de validación (backtest in-sample y out-of-sample, y Monte Carlo) y una simulación antes de cada operación. La infraestructura se define con Terraform y se ajusta el calendario.

## 1. Título

trading_maxxing: plataforma multiestrategia de trading de criptomonedas. Un comité de agentes combina pronósticos de series de tiempo (Kronos) con estrategias cuantitativas validadas, simula cada operación antes de ejecutarla y opera sobre un motor event-driven (NautilusTrader).

## 2. Descripción del proyecto

En trading_maxxing cada usuario (el autor y un equipo de menos de 10 personas) tiene su propio portafolio y solo decide tres cosas: su capital, su nivel de riesgo del 1 al 5 y sus metas. Todo lo demás es compartido: el laboratorio, las estrategias, sus señales y su ciclo de vida se calculan una sola vez para todos, y cada portafolio las ejecuta en la proporción que corresponde a su nivel de riesgo.

Problema. Un inversionista individual no puede vigilar el mercado todo el día. Además, la mayoría de los bots y las estrategias que circulan en redes nunca se validan fuera de los datos con los que se diseñaron, así que el usuario no sabe si tienen ventaja real o si fue suerte.

Solución. Un sistema que:

1. Mantiene una biblioteca de estrategias intercambiables. Todas cumplen el mismo contrato (`analyze(ctx) -> Signal`) y solo se incluyen las que tienen evidencia publicada en criptomonedas, además de Kronos.
2. Valida cada estrategia antes de dejarla operar: backtest in-sample, prueba out-of-sample y Monte Carlo sobre la secuencia de operaciones. Solo las que pasan criterios fijos reciben capital.
3. Asigna a cada estrategia su propio bolsillo de capital (sleeve) dentro de cada portafolio, según los resultados de su validación. El nivel de riesgo del usuario decide qué fracción de cada sleeve se invierte y cuánto queda en USDT.
4. Simula cada operación antes de ejecutarla con trayectorias históricas reconstruidas por bootstrap; en el sleeve de Kronos se suman las trayectorias que genera el propio modelo. Si el valor esperado no es positivo, la bloquea.
5. Controla el riesgo con reglas deterministas: tamaño de posición, stop-loss y take-profit, drawdown máximo y botón de paro.
6. Ejecuta sobre NautilusTrader en Binance Spot Testnet y muestra en un dashboard el portafolio, el P&L, la justificación de cada operación y la probabilidad de alcanzar la meta.

Alcance del MVP. Solo criptomonedas: BTC/USDT y ETH/USDT en Binance Spot Testnet, es decir, paper trading sin dinero real. Al ser spot, solo se puede estar comprado o en USDT: una señal `long` significa comprar o mantener, y `flat` significa vender y quedarse en USDT. Los cortos no se abren. Incluye backtesting con datos históricos, 3 horizontes y 5 niveles de riesgo. Queda fuera del alcance operar con dinero real, acciones o forex, y entrenar modelos propios (solo inferencia).

### 2.1 Biblioteca de estrategias

Una estrategia entra a la biblioteca solo si tiene evidencia publicada en criptomonedas. Aun así, debe pasar nuestra validación (sección 3.2, fase 1) antes de operar.

| Estrategia | Agente | Evidencia | Timeframe |
|---|---|---|---|
| Pronóstico con Kronos | KronosAgent | Shi et al., *Kronos: A Foundation Model for the Language of Financial Markets*, AAAI 2026. En zero-shot, su RankIC de precio es ~93 % mayor que el del mejor modelo base. Sus datos de preentrenamiento llegan a junio de 2024 (apéndice D del paper) | 4 h |
| Trend following (canales Donchian en varios periodos) | TrendAgent | Liu y Tsyvinski, *Risks and Returns of Cryptocurrency*, Review of Financial Studies, 2021 (momentum de serie de tiempo en BTC, ETH y XRP). Zarattini, Pagani y Barbon, *Catching Crypto Trends*, SFI 25-80, 2025 | 1 día |
| Momentum intradía | IntradayMomentumAgent | Shen, Urquhart y Wang, *Bitcoin intraday time-series momentum*, Financial Review, 2022: el retorno de la primera media hora predice el de la última, in-sample y out-of-sample | 30 min |

Se evaluaron y descartaron Noise Band (solo tiene evidencia en SPY), la ruptura de la caja asiática (forex, con ventaja marginal) y el Initial Balance en tercios (no encontramos estudios). Las noticias no son una estrategia: el NewsAgent solo puede vetar entradas.

### 2.2 Código que vamos a reutilizar

NautilusTrader (https://github.com/nautechsystems/nautilus_trader, licencia LGPL-3.0)

- Qué aporta: un motor event-driven con núcleo en Rust que se controla desde Python. Incluye TradingNode para operar en vivo, BacktestEngine, la clase base Strategy, un RiskEngine, cache, message bus con Redis opcional y adaptadores para Binance, entre otros.
- Cómo lo usamos: como dependencia con versión fija. Escribimos AgenticStrategy, que hereda de Strategy, recibe las decisiones del orquestador y las convierte en órdenes bracket. Usamos el adaptador de Binance en modo testnet y BacktestEngine para el laboratorio de validación. El mismo código corre en backtest y en vivo.
- Licencia: la LGPL permite usarlo como librería sin modificar su código.

Kronos (https://github.com/shiyu-coder/Kronos, licencia MIT)

- Qué aporta: un modelo fundacional para velas financieras. Se usa con KronosTokenizer, Kronos y KronosPredictor, cuyos métodos predict() y predict_batch() reciben un DataFrame OHLCV. Es generativo: con `sample_count` devuelve varias trayectorias posibles del precio.
- Cómo lo usamos: lo envolvemos en un microservicio FastAPI (forecast). Usamos Kronos-small (24.7M parámetros, contexto de 512 velas) sobre velas de 4 horas, con unas 20 trayectorias por decisión. Medido en una Mac M5, cada trayectoria tarda unos 0.6 s con aceleración Metal (MPS) y 1.1 s en CPU, porque la implementación no tiene caché de KV; por eso el sleeve de Kronos decide cada 4 horas y sus pronósticos del backtest se precalculan una vez y se guardan en Parquet. El KronosAgent convierte las trayectorias en dirección, confianza y probabilidades de tocar el stop o el objetivo.

Por qué encajan. NautilusTrader resuelve la parte crítica (ejecución, órdenes, riesgo y backtest determinista), pero no incluye interfaz, orquestación ni IA. Kronos aporta el pronóstico, pero su señal por sí sola es débil y no ejecuta operaciones. trading_maxxing construye lo que falta: la biblioteca de estrategias, la validación, la simulación, la configuración por perfil, la interfaz y la infraestructura.

## 3. Componentes y métodos

Para el detalle de la arquitectura y los diagramas, ver la página **[Arquitectura](Arquitectura)**.

| Componente | Responsabilidad | Motor | Métodos principales |
|---|---|---|---|
| web (Next.js) | Onboarding, perfil, dashboard, backtests | — | /onboarding, /dashboard, /strategies, /backtests, /trades |
| api (FastAPI) | API REST, validación, disparo de jobs | — | POST /profiles, GET /portfolio, POST /backtests, POST /bots/{id}/start, POST /bots/{id}/stop, GET /trades |
| InvestmentProfile | Capital, nivel de riesgo (1 a 5) y metas del usuario | Python | validate(), to_risk_limits() |
| InvestmentGoal | Metas de rendimiento | Python | progress(equity), probability(sim) |
| BaseAgent (abstracta) | Contrato común de las estrategias | — | analyze(ctx) -> Signal |
| KronosAgent | Señal y trayectorias a partir de Kronos | Kronos-small | analyze(ctx), trajectories(ctx) |
| TrendAgent | Trend following con canales Donchian | Python | analyze(ctx) |
| IntradayMomentumAgent | Momentum de la primera a la última media hora | Python | analyze(ctx) |
| NewsAgent | Veto ante noticias de alto impacto | LLM (Groq) | fetch_news(symbol), classify(articles), veto(ctx) |
| AgentOrchestrator | Ciclo por vela: corre cada sleeve activo y en shadow | Python | run_cycle(profile), run_sleeve(sleeve, ctx) |
| PortfolioAllocator | Pesos globales por estrategia (validación y correlación) y exposición por usuario según su nivel de riesgo | Python | weights(strategies), allocate(profile, weights) -> dict[str, Decimal] |
| StrategyRegistry | Estado del ciclo de vida de cada estrategia (tabla `strategies`) | Python + Supabase | get(name), transition(name, to, reason), active(), shadow() |
| LifecycleMonitor | Detecta deterioro en vivo y degrada estrategias | Python | check(strategy, live_stats, mc_report) -> Transition |
| SimulationGate | Monte Carlo antes de cada operación | numpy | simulate(decision, paths) -> SimResult, approve(sim) |
| RiskAgent | Tamaño de posición y límites | Python | evaluate(decision, portfolio), position_size(signal, equity), check_drawdown(portfolio) |
| ExplainerAgent | Justificación en lenguaje natural (fuera del camino de la decisión) | LLM | explain(decision) -> str |
| LLMClient | Interfaz común para proveedores de LLM | Groq | complete_json(prompt, schema) |
| KronosForecastService | Inferencia de Kronos | Kronos | load_model(name), predict(ohlcv, pred_len, samples) |
| MarketDataService | Velas históricas y en tiempo real | Python | get_bars(symbol, tf, start, end), stream_bars(symbol, tf) |
| TradingEngineService | Envuelve el TradingNode de Nautilus | Nautilus | start(profile), stop(), submit(decision), get_positions() |
| AgenticStrategy (Strategy de Nautilus) | Convierte decisiones en órdenes | Nautilus | on_start(), on_bar(bar), on_signal(decision), on_order_filled(event), on_stop() |
| BacktestService | Backtests in-sample y out-of-sample | Nautilus | run(config) -> BacktestReport, walk_forward(config) |
| MonteCarloService | Remuestreo de operaciones y proyección de metas | numpy | reshuffle(trades, n), goal_probability(stats, goal) |
| TradeRepository | Persistencia en Supabase | Python | save_trade(trade), list_trades(user_id), save_equity_snapshot(snap) |

Solo el NewsAgent y el ExplainerAgent usan un LLM. Las estrategias, el orquestador, la simulación y el riesgo son deterministas por tres razones:

- Los backtests tienen que ser reproducibles.
- Un LLM conoce precios históricos, lo que contaminaría el backtest con información del futuro.
- El control de riesgo no puede depender de un modelo probabilístico.

LLMClient permite cambiar de proveedor por configuración; por ejemplo, para comparar Groq con modelos de decisión como Jev.

### 3.1 Flujo de una operación

Fase 1, laboratorio (antes de que una estrategia opere):

1. MarketDataService descarga la historia de BTC/USDT y ETH/USDT de Binance.
2. BacktestService corre un walk-forward con comisiones y slippage: optimiza en 12 meses (in-sample), prueba en los 3 siguientes (out-of-sample) y avanza 3 meses, con datos desde 2020 y 20 tramos de prueba de 2021-T1 a 2025-T4. Los tramos out-of-sample se concatenan en una sola curva.
3. Al final se corre una sola vez sobre el holdout (2026, de enero a octubre), que nunca se usa durante el desarrollo.
   - Excepción, Kronos: como ya vio datos hasta junio de 2024, no se evalúa antes de esa fecha. No se reentrena, así que solo se ajustan sus umbrales con julio–diciembre de 2024; 2025 es su out-of-sample y 2026 su holdout.
4. MonteCarloService remuestrea los retornos diarios del sleeve 10,000 veces (block bootstrap, simulaciones de un año) y obtiene la distribución del drawdown máximo y la probabilidad de ruina.
5. La estrategia se aprueba solo si cumple criterios fijos: Sharpe out-of-sample mayor a 0.5, drawdown p95 a 1 año y a tamaño completo por debajo de un techo global de 40 %, y al menos 100 operaciones (o 30 operaciones y 3 años out-of-sample, si es de baja frecuencia). Si los cumple, pasa a **shadow**.

Fase 2, producción (en cada cierre de vela):

1. El orquestador arma el contexto: velas recientes, portafolio y perfil.
2. Cada estrategia activa o en shadow corre en su propio sleeve, en paralelo, cuando cierra una vela de su timeframe.
3. NewsAgent clasifica las noticias recientes y puede vetar nuevas entradas.
4. Las señales se calculan **una sola vez para todos** (capa compartida) y se publican en Redis. Cada bot (el portafolio de un usuario) las ejecuta en su sleeve.
5. SimulationGate calcula, con los SL y TP propuestos, la probabilidad de llegar al objetivo antes que al stop con 10,000 trayectorias históricas reconstruidas por bootstrap.
6. RiskAgent calcula el tamaño de la posición dentro del capital del sleeve y con la exposición del nivel de riesgo del usuario.
7. La decisión se publica en Redis. AgenticStrategy envía una orden bracket a Binance Testnet.
8. La operación se guarda en Supabase, el ExplainerAgent redacta la justificación y el dashboard se actualiza con Supabase Realtime.
9. Mientras la posición está abierta, el SL y el TP la cierran automáticamente. Si se rebasa el drawdown máximo, el kill switch cierra todo y detiene el bot.

### 3.2 Qué es de la app y qué es de cada usuario

| Nivel app (se calcula una vez para todos) | Nivel usuario (por portafolio) |
|---|---|
| Datos de mercado, símbolos (BTCUSDT, ETHUSDT) | Capital (mínimo 500 USDT de testnet) |
| Catálogo de estrategias, laboratorio y criterios de aprobación | Nivel de riesgo del 1 al 5 |
| Señales de cada estrategia, pronósticos de Kronos, SimulationGate | Metas (retorno y fecha) |
| Noticias y vetos | Bot encendido, apagado y paro de emergencia |
| Estado de cada estrategia (shadow, activa, retirada) | Tamaño de cada sleeve y límites del RiskAgent (derivados de su nivel) |
| Pesos globales por estrategia | Órdenes, posiciones, P&L y dashboard (aislados por RLS) |
| Cuenta de Binance Testnet, usuarios y roles | |

Niveles de riesgo:

| Nivel | Drawdown máximo tolerado | Riesgo por operación | Exposición máxima |
|---|---|---|---|
| 1 Conservador | 10 % | 0.25 % | 20 % |
| 2 | 15 % | 0.5 % | 35 % |
| 3 Moderado | 20 % | 1 % | 50 % |
| 4 | 30 % | 1.5 % | 70 % |
| 5 Agresivo | 40 % | 2 % | 90 % |

### 3.3 Ciclo de vida de las estrategias

```
investigación → laboratorio → shadow → activa → en observación → retirada
                    ▲                                               │
                    └────────────── revalidación ───────────────────┘
```

| Estado | Qué hace | Cómo entra |
|---|---|---|
| Investigación | Hipótesis con evidencia publicada en cripto | PR con el código (BaseAgent), sus pruebas y la fuente |
| Laboratorio | Walk-forward + Monte Carlo | CI y comando del laboratorio |
| Shadow | Corre en vivo sin capital y registra lo que habría hecho | Pasa los criterios del laboratorio |
| Activa | Opera su sleeve con capital | Aprobación humana desde el dashboard, tras ≥ 7 días en shadow sin fallas |
| En observación | Opera con la mitad del capital | Automático: su drawdown en vivo rebasa el p95 del Monte Carlo, o el Sharpe de sus últimos 30 trades es negativo |
| Retirada | Sin capital; se archiva con sus reportes | Automático: su drawdown rebasa el p99, o lleva 30 días en observación sin recuperarse |

## 4. Stack tecnológico

- Frontend: Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui y Recharts; se publica como sitio estático en Cloudflare Pages (plan gratuito).
- Backend: FastAPI con Pydantic sobre Python 3.12.
- Estrategias y agentes: worker en Python 3.12 con numpy y pandas. LLM vía Groq (gpt-oss-20b) detrás de una interfaz LLMClient, y noticias de CryptoPanic.
- Pronóstico: Kronos-small con PyTorch en CPU, servido con FastAPI.
- Motor de trading: NautilusTrader.
- Base de datos y autenticación: Supabase (Postgres, Auth, Realtime y Row Level Security).
- Mensajería y cache: Redis (Azure Cache for Redis Basic C0 en la nube, contenedor en local).
- Contenedores: Docker y Docker Compose, con las imágenes publicadas en Docker Hub.
- Infraestructura como código: Terraform con el provider `azurerm` y módulos por recurso (repo separado `trading_maxxing-infra`).
- CI/CD: GitHub Actions, con autenticación a Azure por OIDC (sin llaves de larga duración) y a Cloudflare por API Token.
- Nube: Azure App Service Plan Linux B1 con dos Web Apps de contenedor (`tm-api-<env>` y `tm-agents-<env>`), Application Insights + Log Analytics y Azure Cache for Redis (Basic C0). El frontend se publica en Cloudflare Pages.
- Calidad: pytest y pytest-cov, Vitest y React Testing Library, ruff, ESLint y Prettier.

## 5. Plan de trabajo

El plan está en el [GitHub Project Board](https://github.com/users/CarloSantoro21/projects/2) del repositorio, con columnas Backlog, To Do, In Progress, In Review y Done.

- M1, Fundaciones e infraestructura (hasta el 11 de noviembre): ramas protegidas, estructura del monorepo, Docker Compose, CI básico, Supabase y Wiki.
- M2, Motor, pronóstico y estrategias (hasta el 18 de noviembre): datos de mercado, servicio de Kronos, integración con Nautilus, TrendAgent, IntradayMomentumAgent y laboratorio de backtest con Monte Carlo.
- M3, Agentes, simulación y API (hasta el 24 de noviembre): KronosAgent, NewsAgent, SimulationGate, RiskAgent, orquestador con sleeves, PortfolioAllocator, ciclo de vida de estrategias y API REST.
- M4, Frontend y validación local (hasta el 1 de diciembre): dashboard, onboarding, backtests en la interfaz, pruebas end-to-end en local y validación go/no-go con paper trading continuo.
- M5, Entrega: deploy y demo (hasta el 6 de diciembre): Terraform de Azure, CD del backend a Azure App Service (dev y prod con aprobación), frontend en Cloudflare Pages (dev y prod), Application Insights conectado, documentación y demo final.

Issues: https://github.com/CarloSantoro21/trading_maxxing/issues

Para el estado actual del proyecto (quedan 15 issues abiertos de 53), ver [`docs/estado-del-proyecto.md`](https://github.com/CarloSantoro21/trading_maxxing/blob/main/docs/estado-del-proyecto.md).

## 6. Equipo

Proyecto del curso **Infraestructura para el Desarrollo Continuo** (ITESO, otoño 2026). Equipo de menos de 10 personas con roles rotativos; la lista actual de contribuidores está en [Contributors del repo](https://github.com/CarloSantoro21/trading_maxxing/graphs/contributors).

## 7. Riesgos y mitigaciones

- La API de NautilusTrader v2 puede cambiar entre versiones: fijamos la versión exacta y la imagen base por digest.
- Kronos es lento: decisiones cada 4 horas con ~20 trayectorias, pronósticos del backtest precalculados en Parquet y, como mejora opcional, agregar caché de KV a la implementación del modelo.
- Sobreajuste de estrategias: walk-forward, holdout final intocable y criterios de aprobación fijados de antemano.
- Contaminación de Kronos: sus datos de preentrenamiento llegan a junio de 2024, así que solo se evalúa de julio de 2024 en adelante.
- Comisiones: en Binance Spot cada operación cuesta ~0.1 % por lado. El laboratorio las incluye siempre y compara cada estrategia contra comprar y mantener.
- Señales malas pueden generar pérdidas: operamos solo en testnet, la SimulationGate bloquea operaciones con valor esperado negativo y el RiskAgent aplica el drawdown máximo y un botón de paro.
- Costos en la nube: la cuenta es **Azure for Students** (100 USD de crédito, sin tarjeta). Dos planes App Service B1 (~13 USD al mes cada uno) y Azure Cache for Redis Basic C0; `terraform destroy` del ambiente de dev fuera de ventanas de demo.
- Límites de las APIs de noticias y del LLM: cache en Redis, llamadas solo al cierre de vela y una interfaz LLMClient para cambiar de proveedor.
- Calendario ajustado: las estrategias deterministas se implementan antes que las dependientes de modelos, y el ExplainerAgent y la proyección de metas son opcionales si falta tiempo.

---

trading_maxxing es un proyecto académico. Opera solo en entornos de prueba y no constituye asesoría financiera.
