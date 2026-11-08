"""Worker engine: arranca TradingEngineService y escribe un heartbeat con su estado.

python -m tm_engine           # corre en bucle (Binance Spot Testnet o modo sin conexión)
python -m tm_engine --once    # imprime su estado y termina

El heartbeat (`HEARTBEAT_FILE`, por defecto /tmp/heartbeat) es un JSON con la hora y el
estado del motor: `{"ts": ..., "mode": "testnet" | "offline", "connected": ..., ...}`. El
healthcheck del contenedor solo revisa que se actualice.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from tm_shared.health import health

from tm_engine.service import Credentials, EngineProfile, TradingEngineService

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


def recorder_from_env() -> Any | None:
    """Supabase si hay service role y `ENGINE_BOT_ID`; si no, fills solo en memoria."""
    bot_id = os.environ.get("ENGINE_BOT_ID")
    if not bot_id or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return None
    from tm_engine.recorder import SupabaseTradeRecorder

    return SupabaseTradeRecorder(bot_id)


def sleeve_guard_from_env() -> Any | None:
    """Guardia de sleeves con el registro de Supabase: shadow nunca envía órdenes.

    Sin Supabase no hay registro que consultar y el motor no filtra (modo de desarrollo).
    """
    if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
        return None
    from tm_agents.reference import TradingSleeveGuard
    from tm_agents.registry import PostgRESTBackend, StrategyRegistry

    return TradingSleeveGuard(StrategyRegistry(PostgRESTBackend.from_env()))


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(prog="tm_engine").parse_known_args(argv)[1]
    logging.basicConfig(
        stream=sys.stdout, level=os.environ.get("LOG_LEVEL", "INFO").upper(), force=False
    )
    mode = "testnet" if Credentials.from_env() else "offline"
    print(json.dumps({**health("engine").model_dump(), "mode": mode}), flush=True)
    if "--once" in args:
        return

    service = TradingEngineService(
        redis_client=redis_from_env(),
        recorder=recorder_from_env(),
        sleeve_guard=sleeve_guard_from_env(),
    )
    service.start(EngineProfile.from_env())
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            beat(service.status())
            time.sleep(INTERVAL)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
