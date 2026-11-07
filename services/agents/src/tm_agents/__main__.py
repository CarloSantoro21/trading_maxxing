"""Worker agents: ciclo principal con heartbeat (la lógica real llega en los issues de M2 y M3).

python -m tm_agents           # corre en bucle
python -m tm_agents --once    # imprime su estado y termina
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from tm_shared.health import health

HEARTBEAT = Path(os.environ.get("HEARTBEAT_FILE", "/tmp/heartbeat"))
INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "10"))


def beat() -> None:
    HEARTBEAT.write_text(str(time.time()))


def main(argv: list[str] | None = None) -> None:
    args = argparse.ArgumentParser(prog="tm_agents").parse_known_args(argv)[1]
    print(json.dumps(health("agents").model_dump()), flush=True)
    if "--once" in args:
        return
    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        beat()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
