"""Código de Kronos incorporado (vendor) desde https://github.com/shiyu-coder/Kronos (MIT).

Ver README.md de esta carpeta para el commit de origen y los cambios locales.
"""

from .kronos import Kronos, KronosPredictor, KronosTokenizer

__all__ = ["Kronos", "KronosPredictor", "KronosTokenizer"]
