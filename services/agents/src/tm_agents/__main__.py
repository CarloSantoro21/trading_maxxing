"""Worker agents: ciclo principal con heartbeat y el ledger de referencia de las estrategias.

python -m tm_agents           # corre en bucle
python -m tm_agents --once    # imprime su estado y termina

Con ``REDIS_URL``, ``SUPABASE_URL`` y ``SUPABASE_SERVICE_ROLE_KEY`` corre además el
``ReferenceService`` (#57): lee ``tm:signals``, lleva el portafolio de referencia de cada
estrategia en shadow, active y probation, y aplica las degradaciones del LifecycleMonitor.
Sin esas variables solo escribe el heartbeat.
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

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))
INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "10"))

log = logging.getLogger("tm_agents")


def beat() -> None:
    HEARTBEAT.write_text(str(time.time()))


def reference_service_from_env(env: dict[str, str] | None = None) -> Any | None:
    """``ReferenceService`` con Redis y Supabase; ``None`` si falta alguna variable."""
    env = dict(os.environ) if env is None else env
    url = env.get("REDIS_URL")
    if not url or not env.get("SUPABASE_URL") or not env.get("SUPABASE_SERVICE_ROLE_KEY"):
        return None
    import redis

    from tm_agents.reference import (
        PostgRESTReferenceStore,
        RedisStreamSignalSource,
        ReferenceService,
    )
    from tm_agents.reference.signals import REFERENCE_CHECKPOINT
    from tm_agents.registry import PostgRESTBackend, StrategyRegistry

    client = redis.Redis.from_url(url)
    return ReferenceService(
        StrategyRegistry(PostgRESTBackend.from_env(env)),
        PostgRESTReferenceStore.from_env(env),
        RedisStreamSignalSource(client, checkpoint_key=REFERENCE_CHECKPOINT),
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
    reference = None
    try:
        reference = reference_service_from_env()
    except Exception as exc:  # sin Redis o Supabase el worker sigue con su heartbeat
        log.warning("no se pudo arrancar el ledger de referencia: %s", exc)
    while running:
        beat()
        if reference is not None:
            try:
                reference.run_once()
            except Exception:
                log.exception("falló el ciclo del ledger de referencia")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
