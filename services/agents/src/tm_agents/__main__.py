"""Worker agents: orquestador (capa compartida + un BotRunner por bot) con heartbeat.

python -m tm_agents           # corre en bucle
python -m tm_agents --once    # imprime su estado y termina

Con ``REDIS_URL`` corre las dos capas del orquestador (ver ``tm_agents.orchestrator``):

- capa compartida: estrategias del registro de Supabase (``SUPABASE_URL`` y
  ``SUPABASE_SERVICE_ROLE_KEY``), velas de ``MarketDataService`` y vetos de Redis; publica en
  ``tm:signals``. Se apaga con ``ORCHESTRATOR_SHARED=0`` (por ejemplo, si se separa en otro
  contenedor).
- capa por usuario: lee ``tm:signals`` y ``tm:commands``, bots en ``running`` de la tabla
  ``bots``; publica en ``tm:decisions`` y ``tm:shadow``. Se apaga con ``ORCHESTRATOR_BOTS=0``.

Sin ``REDIS_URL`` solo escribe el heartbeat (desarrollo y pruebas).
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


def build_worker(env: Mapping[str, str] | None = None) -> Any | None:
    """Arma el ``OrchestratorWorker`` desde el entorno; ``None`` si no hay ``REDIS_URL``."""
    env = os.environ if env is None else env
    url = env.get("REDIS_URL")
    if not url:
        log.info("sin REDIS_URL: el orquestador no corre (solo heartbeat)")
        return None
    import redis
    from tm_marketdata.service import MarketDataService

    from tm_agents.news.veto import RedisVetoStore
    from tm_agents.orchestrator.runtime import OrchestratorWorker
    from tm_agents.orchestrator.shared import (
        MarketDataBarsProvider,
        SharedSignalLayer,
        StoreVetoProvider,
    )
    from tm_agents.orchestrator.streams import (
        DECISIONS_STREAM,
        SHADOW_STREAM,
        SIGNALS_STREAM,
        RedisSignalSource,
        RedisStreamSink,
    )
    from tm_agents.orchestrator.supervisor import (
        BotSupervisor,
        CommandListener,
        InMemoryBotRepository,
        PostgRESTBotRepository,
    )
    from tm_agents.registry import InMemoryBackend, PostgRESTBackend, StrategyRegistry

    client = redis.Redis.from_url(url)
    has_supabase = bool(env.get("SUPABASE_URL") and env.get("SUPABASE_SERVICE_ROLE_KEY"))
    symbols = [s.strip().upper() for s in env.get("ORCHESTRATOR_SYMBOLS", "").split(",")]
    symbols = [s for s in symbols if s] or ["BTCUSDT", "ETHUSDT"]

    shared = None
    if _flag(env, "ORCHESTRATOR_SHARED"):
        backend = PostgRESTBackend.from_env(env) if has_supabase else InMemoryBackend()
        shared = SharedSignalLayer(
            StrategyRegistry(backend),
            MarketDataBarsProvider(MarketDataService(data_dir=env.get("DATA_DIR") or None)),
            RedisStreamSink(client, SIGNALS_STREAM),
            symbols=symbols,
            news=StoreVetoProvider(RedisVetoStore(client)),
        )
    repository = PostgRESTBotRepository.from_env(env) if has_supabase else InMemoryBotRepository()
    supervisor = BotSupervisor(
        repository,
        RedisStreamSink(client, DECISIONS_STREAM),
        shadow_sink=RedisStreamSink(client, SHADOW_STREAM),
    )
    bots = _flag(env, "ORCHESTRATOR_BOTS")
    return OrchestratorWorker(
        shared,
        supervisor if bots else BotSupervisor(InMemoryBotRepository(), _NullSink()),
        RedisSignalSource(client) if bots else _NoSignals(),
        CommandListener(client) if bots else None,
    )


class _NullSink:
    def publish(self, item: object) -> None:
        return None


class _NoSignals:
    def poll(self, now: object = None) -> list:
        return []


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
        log.exception("no se pudo armar el orquestador; solo heartbeat")
        worker = None
    sleep = min(INTERVAL, TICK) if worker is not None else INTERVAL
    last_beat = 0.0
    while running:
        if time.monotonic() - last_beat >= INTERVAL or worker is None:
            beat()
            last_beat = time.monotonic()
        if worker is not None:
            try:
                worker.tick()
            except Exception:
                log.exception("falló un ciclo del orquestador; se reintenta")
        time.sleep(sleep)


if __name__ == "__main__":
    main()
