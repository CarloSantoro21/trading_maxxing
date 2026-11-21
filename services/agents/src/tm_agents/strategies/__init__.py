"""Biblioteca de estrategias. Importar este paquete registra todas las estrategias."""

from tm_agents.strategies.example_sma_cross import ExampleSmaCrossAgent
from tm_agents.strategies.intraday_momentum import IntradayMomentumAgent
from tm_agents.strategies.kronos import KronosAgent
from tm_agents.strategies.moving_average import MovingAverageAgent
from tm_agents.strategies.trend import TrendAgent
from tm_agents.strategies.tsmom import TsmomAgent

__all__ = [
    "ExampleSmaCrossAgent",
    "IntradayMomentumAgent",
    "KronosAgent",
    "MovingAverageAgent",
    "TrendAgent",
    "TsmomAgent",
]
