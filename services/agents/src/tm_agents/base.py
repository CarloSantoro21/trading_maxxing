"""Contrato de la biblioteca de estrategias: `BaseAgent` y el registro de estrategias.

Una estrategia nueva es una subclase de `BaseAgent` decorada con `@register_strategy`.
El orquestador construye las activas a partir de la configuración del bot, sin saber cómo
piensa cada una: todas reciben un `AgentContext` y devuelven un `Signal`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import pandas as pd
from tm_shared.contracts import Horizon, Signal, Timeframe


@dataclass(frozen=True)
class AgentContext:
    """Lo que ve una estrategia en un ciclo: velas cerradas por timeframe hasta `now`."""

    symbol: str
    now: datetime
    bars: Mapping[Timeframe, pd.DataFrame]
    horizon: Horizon = Horizon.MEDIUM
    extra: Mapping[str, Any] = field(default_factory=dict)

    def bars_for(self, tf: Timeframe) -> pd.DataFrame:
        try:
            return self.bars[tf]
        except KeyError as exc:
            raise KeyError(f"el contexto no trae velas de {tf.value} para {self.symbol}") from exc


class BaseAgent(ABC):
    """Una estrategia de la biblioteca. Debe ser determinista dado el mismo contexto."""

    name: ClassVar[str]
    timeframe: ClassVar[Timeframe]

    def __init__(self, **params: Any) -> None:
        self.params = params

    @abstractmethod
    def analyze(self, ctx: AgentContext) -> Signal:
        """Devuelve la opinión de la estrategia para `ctx.symbol` en `ctx.now`."""


_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_strategy(name: str) -> Callable[[type[BaseAgent]], type[BaseAgent]]:
    """Decorador que registra una estrategia bajo `name`."""

    def deco(cls: type[BaseAgent]) -> type[BaseAgent]:
        if not issubclass(cls, BaseAgent):
            raise TypeError(f"{cls.__name__} no hereda de BaseAgent")
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            raise ValueError(f"ya hay una estrategia registrada como '{name}'")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def registered_strategies() -> dict[str, type[BaseAgent]]:
    return dict(_REGISTRY)


def build_strategies(config: Mapping[str, Mapping[str, Any]]) -> list[BaseAgent]:
    """Construye las estrategias activas.

    `config` tiene la forma `{"trend": {"enabled": true, "lookbacks": [20, 50]}, ...}`;
    lo que no sea `enabled` se pasa como parámetro a la estrategia.
    """
    agents: list[BaseAgent] = []
    for name, cfg in config.items():
        if not cfg.get("enabled", True):
            continue
        if name not in _REGISTRY:
            raise KeyError(f"estrategia desconocida: '{name}' (registradas: {sorted(_REGISTRY)})")
        params = {k: v for k, v in cfg.items() if k != "enabled"}
        agents.append(_REGISTRY[name](**params))
    return agents
