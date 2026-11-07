"""Punto de entrada del worker agents (se completa en los issues de M2 y M3)."""

import json

from tm_shared.health import health


def main() -> None:
    print(json.dumps(health("agents").model_dump()))


if __name__ == "__main__":
    main()
