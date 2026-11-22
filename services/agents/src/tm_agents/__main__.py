"""Worker agents: orquestador (capa compartida + un BotRunner por bot), ledger de referencia
y heartbeat, en un solo proceso.

python -m tm_agents           # corre en bucle
python -m tm_agents --once    # imprime su estado y termina

Todo lo persistente vive en D1 detrás de la API en Cloudflare Workers (``/internal/*``, ver
``docs/arquitectura/api.md``). El motor solo hace conexiones salientes con ``TM_API_URL``,
``TM_SERVICE_TOKEN`` (header ``X-Service-Token``) y, si existen, ``CF_ACCESS_CLIENT_ID`` y
``CF_ACCESS_CLIENT_SECRET`` (service token de Cloudflare Access).

Con ``REDIS_URL`` corre las dos capas del orquestador (ver ``tm_agents.orchestrator``):

- capa compartida: estrategias del registro (``/internal/strategies``), velas de
  ``MarketDataService`` y vetos de Redis; publica en ``tm:signals`` (formato de
  :mod:`tm_agents.signals`). Se apaga con ``ORCHESTRATOR_SHARED=0``.
- capa por usuario: lee ``tm:signals``, los bots en ``running`` (``/internal/bots``) y los
  comandos ``start``/``stop``/``kill`` de la cola de la API (``GET /internal/commands`` cada
  ``COMMANDS_POLL_SECONDS``, 5 s, y ``ack``); publica en ``tm:decisions`` y ``tm:shadow`` y
  copia cada decisión a ``POST /internal/decisions`` (``DECISIONS_AUDIT=0`` lo apaga). Cada
  ``EQUITY_SNAPSHOT_SECONDS`` (300 s) manda la equity de cada bot a
  ``/internal/equity-snapshots``. Se apaga con ``ORCHESTRATOR_BOTS=0``.
- ExplainerAgent: corre en su propio hilo con event loop (:mod:`tm_agents.explainer_runtime`)
  y guarda cada explicación con ``POST /internal/decisions`` (``kind = "explanation"``).

Con ``REDIS_URL`` y la API corre además el ``ReferenceService`` (#57): lee el mismo
``tm:signals`` con su propio cursor (``tm:reference:last_signal_id``), lleva el portafolio de
referencia de cada estrategia en shadow, active y probation, y aplica las degradaciones del
LifecycleMonitor. Se apaga con ``REFERENCE_SERVICE=0``. Corre cada ``HEARTBEAT_INTERVAL``
segundos.

Con la API, el latido va a ``POST /internal/heartbeat`` (componente ``agents``) cada
``TM_HEARTBEAT_SECONDS`` (30 s); el frontend lo muestra en ``/api/engine/status``. Sin la API
el orquestador corre con registro y bots en memoria (desarrollo).

Noticias y ExplainerAgent (#118) son **opcionales y están apagados por defecto**:
``NEWS_ENABLED=false`` (no se arma el NewsAgent y la capa compartida recibe ``news=None``) y
``EXPLAINER_ENABLED=false`` (plantilla determinista). ``LLM_PROVIDER=none`` por defecto. Se
pueden prender en caliente con ``SETTINGS_SOURCE=worker-api`` (interruptor del admin en D1,
ver :mod:`tm_agents.orchestrator.settings`).

Sin ``REDIS_URL`` solo escribe el heartbeat (archivo y, con la API, ``/internal/heartbeat``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tm_shared.api_client import HeartbeatReporter, WorkerApiClient, api_configured
from tm_shared.health import health

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))
INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "10"))
TICK = float(os.environ.get("ORCHESTRATOR_TICK_SECONDS", "1"))

log = logging.getLogger("tm_agents")


def beat() -> None:
    HEARTBEAT.write_text(str(time.time()))


def _flag(env: Mapping[str, str], name: str, default: bool = True) -> bool:
    value = env.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def api_from_env(env: Mapping[str, str] | None = None) -> WorkerApiClient | None:
    """Cliente de la API en Workers; ``None`` sin ``TM_API_URL``/``TM_SERVICE_TOKEN``."""
    env = os.environ if env is None else env
    if not api_configured(env):
        return None
    return WorkerApiClient.from_env(env)


def build_worker(env: Mapping[str, str] | None = None) -> Any | None:
    """Arma el ``OrchestratorWorker`` desde el entorno; ``None`` si no hay ``REDIS_URL``."""
    env = os.environ if env is None else env
    url = env.get("REDIS_URL")
    if not url:
        log.info("sin REDIS_URL: el orquestador no corre (solo heartbeat)")
        return None
    import redis
    from tm_marketdata.service import MarketDataService

    from tm_agents.explainer_runtime import ExplainerLoop, WorkerApiExplanationSink
    from tm_agents.orchestrator.equity import EquitySnapshotReporter
    from tm_agents.orchestrator.runtime import OrchestratorWorker
    from tm_agents.orchestrator.shared import MarketDataBarsProvider, SharedSignalLayer
    from tm_agents.orchestrator.state import WorkerApiTradeStateRepository
    from tm_agents.orchestrator.streams import (
        DECISIONS_STREAM,
        SHADOW_STREAM,
        FanoutSink,
        RedisStreamSink,
        WorkerApiDecisionSink,
    )
    from tm_agents.orchestrator.supervisor import (
        BotSupervisor,
        InMemoryBotRepository,
        WorkerApiBotRepository,
        WorkerApiCommandListener,
    )
    from tm_agents.registry import InMemoryBackend, StrategyRegistry, WorkerApiBackend
    from tm_agents.signals import RedisSignalSink, RedisSignalSource

    client = redis.Redis.from_url(url)
    api = api_from_env(env)
    if api is None:
        log.warning("sin TM_API_URL/TM_SERVICE_TOKEN: registro y bots en memoria (desarrollo)")
    symbols = [s.strip().upper() for s in env.get("ORCHESTRATOR_SYMBOLS", "").split(",")]
    symbols = [s for s in symbols if s] or ["BTCUSDT", "ETHUSDT"]

    settings, llm, explainer = build_optional_ai(env)

    shared = None
    news = None
    if _flag(env, "ORCHESTRATOR_SHARED"):
        backend = WorkerApiBackend(api) if api is not None else InMemoryBackend()
        shared = SharedSignalLayer(
            StrategyRegistry(backend),
            MarketDataBarsProvider(MarketDataService(data_dir=env.get("TM_DATA_DIR") or None)),
            RedisSignalSink(client),
            symbols=symbols,
            news=None,  # lo conecta NewsService solo si news_enabled está prendido
        )
        news = news_service_from_env(env, settings, llm, symbols, layer=shared)
    if not _flag(env, "ORCHESTRATOR_BOTS"):
        return OrchestratorWorker(
            shared, BotSupervisor(InMemoryBotRepository(), _NullSink()), _NoSignals(), news=news
        )

    decisions: Any = RedisStreamSink(client, DECISIONS_STREAM)
    shadow: Any = RedisStreamSink(client, SHADOW_STREAM)
    if api is not None and _flag(env, "DECISIONS_AUDIT"):
        # Auditoría en D1 (GET /api/decisions); Redis sigue siendo el camino hacia el motor.
        decisions = FanoutSink(decisions, WorkerApiDecisionSink(api, kind="decision"))
        shadow = FanoutSink(shadow, WorkerApiDecisionSink(api, kind="shadow"))
    explain_loop = explanation_sink = None
    if api is not None:
        # El worker es síncrono: el ExplainerAgent corre en su propio hilo con event loop.
        explain_loop = ExplainerLoop().start()
        explanation_sink = WorkerApiExplanationSink(api)
    supervisor = BotSupervisor(
        WorkerApiBotRepository(api) if api is not None else InMemoryBotRepository(),
        decisions,
        shadow_sink=shadow,
        # Cada bot reconstruye su libro desde la tabla trades al arrancar (#89).
        trades=WorkerApiTradeStateRepository(api) if api is not None else None,
        explainer=explainer,
        explanation_sink=explanation_sink,
        explain_executor=explain_loop,
    )
    commands = equity = None
    if api is not None:
        commands = WorkerApiCommandListener(
            api, interval=float(env.get("COMMANDS_POLL_SECONDS") or 5)
        )
        equity = EquitySnapshotReporter(
            api, supervisor, interval=float(env.get("EQUITY_SNAPSHOT_SECONDS") or 300)
        )
    return OrchestratorWorker(
        shared,
        supervisor,
        RedisSignalSource(client),
        commands,
        news=news,
        equity=equity,
        explain_loop=explain_loop,
    )


def heartbeat_from_env(
    worker: Any | None = None, env: Mapping[str, str] | None = None
) -> HeartbeatReporter | None:
    """Latido ``agents`` en ``/internal/heartbeat``; ``None`` sin API."""
    env = os.environ if env is None else env
    api = api_from_env(env)
    if api is None:
        return None

    def details() -> dict[str, Any]:
        supervisor = getattr(worker, "supervisor", None)
        runners = getattr(supervisor, "runners", None) or {}
        return {"orchestrator": worker is not None, "bots": sorted(runners)}

    return HeartbeatReporter(
        api, "agents", interval=float(env.get("TM_HEARTBEAT_SECONDS") or 30), details=details
    )


def build_optional_ai(env: Mapping[str, str]) -> tuple[Any, Any | None, Any]:
    """Interruptores, cliente de LLM (``None`` con ``LLM_PROVIDER=none``) y ExplainerAgent.

    Si el proveedor elegido no tiene sus llaves, se registra y se sigue **sin LLM**: las
    noticias caen al clasificador por palabras clave y el explainer a la plantilla.
    """
    from tm_agents.explainer import explainer_from_env
    from tm_agents.llm import build_llm_client, llm_provider
    from tm_agents.orchestrator.settings import settings_from_env

    settings = settings_from_env(env)
    try:
        llm = build_llm_client(env)
    except Exception as exc:
        log.warning("LLM_PROVIDER=%s sin configurar; se sigue sin LLM (%s)", llm_provider(env), exc)
        llm = None
    explainer = explainer_from_env(
        env, llm=llm, enabled=lambda: settings.get_bool("explainer_enabled")
    )
    log.info(
        "IA opcional: LLM_PROVIDER=%s, noticias=%s, explainer=%s",
        llm_provider(env) if llm is not None else "none",
        "on" if settings.get_bool("news_enabled") else "off",
        "on" if explainer.uses_llm() else "plantilla",
    )
    return settings, llm, explainer


def news_service_from_env(
    env: Mapping[str, str],
    settings: Any,
    llm: Any | None,
    symbols: list[str],
    *,
    layer: Any | None = None,
) -> Any:
    """``NewsService`` que arma el NewsAgent solo cuando ``news_enabled`` está prendido."""
    from tm_agents.news import news_agent_from_env
    from tm_agents.news.service import NewsService

    return NewsService(
        lambda: settings.get_bool("news_enabled"),
        lambda: news_agent_from_env(env, llm=llm),
        symbols,
        layer=layer,
        interval=float(env.get("NEWS_REFRESH_SECONDS") or 300),
    )


class _NullSink:
    def publish(self, item: object) -> None:
        return None


class _NoSignals:
    def poll(self, now: object = None) -> list:
        return []


def reference_service_from_env(env: Mapping[str, str] | None = None) -> Any | None:
    """``ReferenceService`` con Redis y la API; ``None`` si falta alguna variable o si
    ``REFERENCE_SERVICE=0``."""
    env = os.environ if env is None else env
    url = env.get("REDIS_URL")
    if not url or not api_configured(env):
        return None
    if not _flag(env, "REFERENCE_SERVICE"):
        log.info("REFERENCE_SERVICE=0: el ledger de referencia no corre")
        return None
    import redis

    from tm_agents.reference import (
        REFERENCE_CHECKPOINT,
        ReferenceService,
        WorkerApiReferenceStore,
    )
    from tm_agents.registry import StrategyRegistry, WorkerApiBackend
    from tm_agents.signals import RedisSignalSource

    client = redis.Redis.from_url(url)
    api = WorkerApiClient.from_env(env)
    return ReferenceService(
        StrategyRegistry(WorkerApiBackend(api)),
        WorkerApiReferenceStore(api),
        RedisSignalSource(client, checkpoint_key=REFERENCE_CHECKPOINT),
    )


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(prog="tm_agents").parse_known_args(argv)[1]
    print(json.dumps(health("agents").model_dump()), flush=True)
    if "--once" in args:
        return
    logging.basicConfig(
        stream=sys.stdout, level=os.environ.get("LOG_LEVEL", "INFO").upper(), force=False
    )
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        worker = build_worker()
    except Exception:
        log.exception("no se pudo armar el orquestador; sigue sin él")
        worker = None
    reference = None
    try:
        reference = reference_service_from_env()
    except Exception as exc:  # sin Redis o la API el worker sigue con su heartbeat
        log.warning("no se pudo arrancar el ledger de referencia: %s", exc)
    try:
        heartbeat = heartbeat_from_env(worker)
    except Exception as exc:
        log.warning("no se pudo armar el latido hacia la API: %s", exc)
        heartbeat = None
    sleep = min(INTERVAL, TICK) if worker is not None else INTERVAL
    last_beat = float("-inf")
    while running:
        if time.monotonic() - last_beat >= INTERVAL or worker is None:
            # Heartbeat y ledger de referencia a la cadencia de HEARTBEAT_INTERVAL.
            beat()
            last_beat = time.monotonic()
            if heartbeat is not None:
                heartbeat.maybe_beat()
            if reference is not None:
                try:
                    reference.run_once()
                except Exception:
                    log.exception("falló el ciclo del ledger de referencia")
        if worker is not None:
            try:
                worker.tick()
            except Exception:
                log.exception("falló un ciclo del orquestador; se reintenta")
        if not running:
            break
        time.sleep(sleep)
    if heartbeat is not None:
        heartbeat.beat("stopping")
    explain_loop = getattr(worker, "explain_loop", None)
    if explain_loop is not None:
        explain_loop.stop()


if __name__ == "__main__":
    main()
