"""Worker engine: arranca TradingEngineService y escribe un heartbeat con su estado.

python -m tm_engine           # corre en bucle (Binance Spot Testnet, simulado o sin conexión)
python -m tm_engine --once    # imprime su estado y termina

El heartbeat (`HEARTBEAT_FILE`, por defecto /tmp/heartbeat) es un JSON con la hora y el
estado del motor: `{"ts": ..., "mode": "testnet" | "sim" | "offline", "connected": ..., ...}`.
El healthcheck del contenedor solo revisa que se actualice.

`ENGINE_EXECUTION` elige cómo se ejecutan las decisiones: `auto` (por omisión) usa Binance
Spot Testnet si hay `BINANCE_TESTNET_API_KEY`/`SECRET` y, si faltan, la ejecución **simulada**
(`tm_engine.sim`: fills al precio público de Binance con la comisión del laboratorio);
`testnet`, `sim` u `offline` lo fuerzan.

Con `TM_API_URL` y `TM_SERVICE_TOKEN` (API en Cloudflare Workers, ver
`docs/arquitectura/motor-en-la-mac.md`) el motor además:

- escribe los fills en `trades` (`WorkerApiTradeRecorder`) y restaura las posiciones
  abiertas al arrancar;
- consulta el registro de estrategias para que un sleeve en shadow nunca mande órdenes;
- manda su latido a `POST /internal/heartbeat` cada `TM_HEARTBEAT_SECONDS` (30 s) para
  `/api/engine/status`;
- corre los backtests encolados (`GET /internal/backtests?status=queued`) cada
  `BACKTEST_POLL_SECONDS` (60 s; `BACKTEST_JOBS=0` lo apaga).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tm_shared.api_client import HeartbeatReporter, WorkerApiClient, api_configured
from tm_shared.health import health

from tm_engine.logging_setup import setup_json_logging
from tm_engine.service import EngineProfile, TradingEngineService, resolve_execution

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))
INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "10"))

log = logging.getLogger("tm_engine")


def beat(status: dict[str, Any] | None = None) -> None:
    HEARTBEAT.write_text(json.dumps({"ts": time.time(), **(status or {})}))


def redis_from_env() -> Any | None:
    """Cliente de Redis desde `REDIS_URL`; `None` si no está configurado."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis

    return redis.Redis.from_url(url)


def api_from_env(env: Mapping[str, str] | None = None) -> WorkerApiClient | None:
    """Cliente de la API (`TM_API_URL`, `TM_SERVICE_TOKEN`); `None` si no está configurada."""
    env = os.environ if env is None else env
    if not api_configured(env):
        log.warning("sin TM_API_URL/TM_SERVICE_TOKEN: fills solo en memoria y sin latido")
        return None
    return WorkerApiClient.from_env(env)


def recorder_from_env(
    api: WorkerApiClient | None = None, env: Mapping[str, str] | None = None
) -> Any | None:
    """Fills en `trades` vía la API si está configurada; si no, solo en memoria.

    Cada fill se escribe para el bot de su decisión (#19). `ENGINE_BOT_ID` es opcional: es el
    bot de respaldo para decisiones sin `bot_id` (un solo portafolio).
    """
    env = os.environ if env is None else env
    if api is None:
        return None
    from tm_engine.recorder import WorkerApiTradeRecorder

    return WorkerApiTradeRecorder(env.get("ENGINE_BOT_ID") or None, client=api)


def sleeve_guard_from_env(api: WorkerApiClient | None = None) -> Any | None:
    """Guardia de sleeves con el registro de estrategias: shadow nunca envía órdenes.

    Sin API no hay registro que consultar y el motor no filtra (modo de desarrollo).
    """
    if api is None:
        return None
    from tm_agents.reference import TradingSleeveGuard
    from tm_agents.registry import StrategyRegistry, WorkerApiBackend

    return TradingSleeveGuard(StrategyRegistry(WorkerApiBackend(api)))


def backtests_from_env(
    api: WorkerApiClient | None = None, env: Mapping[str, str] | None = None
) -> Any | None:
    """`BacktestJobRunner` si hay API y `BACKTEST_JOBS` no está apagado."""
    env = os.environ if env is None else env
    if api is None or (env.get("BACKTEST_JOBS") or "1").strip().lower() in ("0", "false", "off"):
        return None
    from tm_engine.backtest_jobs import BacktestJobRunner, market_loader, run_backtest_config

    loader = market_loader(env.get("TM_DATA_DIR") or None)
    return BacktestJobRunner(
        api,
        lambda config: run_backtest_config(config, loader=loader),
        interval=float(env.get("BACKTEST_POLL_SECONDS") or 60),
    )


def heartbeat_from_env(
    api: WorkerApiClient | None, service: Any, env: Mapping[str, str] | None = None
) -> HeartbeatReporter | None:
    env = os.environ if env is None else env
    if api is None:
        return None
    return HeartbeatReporter(
        api,
        "engine",
        interval=float(env.get("TM_HEARTBEAT_SECONDS") or 30),
        details=service.status,
    )


def _engine_status(status: Mapping[str, Any]) -> str:
    if status.get("error") or status.get("restore_error"):
        return "degraded"
    return "ok"


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(prog="tm_engine").parse_known_args(argv)[1]
    setup_json_logging(service="engine", level=os.environ.get("LOG_LEVEL", "INFO"))
    mode = resolve_execution().value
    print(json.dumps({**health("engine").model_dump(), "mode": mode}), flush=True)
    if "--once" in args:
        return

    profile = EngineProfile.from_env()
    log.info(
        "Starting TradingNode" if mode == "testnet" else "Starting engine",
        extra={"mode": mode, "symbols": list(profile.symbols), "timeframe": profile.timeframe},
    )
    api = api_from_env()
    service = TradingEngineService(
        redis_client=redis_from_env(),
        recorder=recorder_from_env(api),
        sleeve_guard=sleeve_guard_from_env(api),
        execution=mode,
    )
    service.start(profile)
    heartbeat = heartbeat_from_env(api, service)
    backtests = backtests_from_env(api)
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stopped = threading.Event()
    if backtests is not None:
        backtests.start(stopped)
    try:
        while running:
            status = service.status()
            beat(status)
            if heartbeat is not None:
                heartbeat.maybe_beat(_engine_status(status))
            time.sleep(INTERVAL)
    finally:
        stopped.set()
        if heartbeat is not None:
            heartbeat.beat("stopping")
        service.stop()
        if api is not None:
            api.close()


if __name__ == "__main__":
    main()
