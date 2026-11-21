"""ExplainerAgent (#37): redacta una justificación en lenguaje natural a partir del rationale.

Está **fuera del camino de la decisión**: se ejecuta de forma asíncrona *después* de que la
decisión ya se publicó y se persistió. Si falla —LLM caído, respuesta inválida o cualquier
excepción— cae a una plantilla determinista compuesta a partir de los campos del propio
rationale. La invariante clave es que *no altera ningún número*: la narrativa solo describe
lo que el orquestador ya decidió.

Uso típico (fire-and-forget desde el BotRunner):

    explainer = ExplainerAgent(llm)
    asyncio.create_task(explainer.explain(envelope.decision.rationale.model_dump()))

Para contextos que no pueden ``await`` hay un shim bloqueante :meth:`explain_sync`.

**Apagado por defecto** (``EXPLAINER_ENABLED=false``): sin LLM ni costo, siempre se usa la
plantilla. ``enabled`` permite prenderlo en caliente (interruptor ``explainer_enabled`` de
:mod:`tm_agents.orchestrator.settings`); ver :func:`explainer_from_env`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from tm_agents.llm.base import LLMClient

log = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Eres un analista que redacta la justificación de una operación de trading en español, "
    "en 1-3 oraciones, para un tablero de usuario. Describe únicamente lo que ya se decidió, "
    "usando los números del rationale tal cual: no inventes cifras, no recomiendes nada y no "
    "cambies el signo de la operación. Devuelve solo JSON con un único campo 'text'."
)


class ExplainerResponse(BaseModel):
    """Esquema que el LLM debe devolver.

    Deliberadamente no fijamos un ``max_length`` en el esquema: el recorte duro vive del
    lado del ``ExplainerAgent`` (``max_text_length``), para que una respuesta larga del LLM
    no se descarte entera, sino que se trunque al tope del cliente.
    """

    text: str = Field(min_length=1)


class ExplainerAgent:
    """Produce una justificación en lenguaje natural para un ``Rationale`` ya calculado.

    Args:
        llm: cliente de LLM (``LLMClient``, reutiliza el mismo que usa el NewsAgent).
        max_text_length: tope para el texto que devuelve el LLM; si lo rebasa, se recorta.
        enabled: interruptor consultado en cada llamada; si devuelve ``False`` se usa la
            plantilla sin tocar el LLM. ``None`` = siempre prendido (si hay ``llm``).
    """

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        max_text_length: int = 600,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._llm = llm
        self._max_text_length = max_text_length
        self._enabled = enabled

    def uses_llm(self) -> bool:
        """True si la próxima explicación irá al LLM (hay cliente y el interruptor está on)."""
        if self._llm is None:
            return False
        if self._enabled is None:
            return True
        try:
            return bool(self._enabled())
        except Exception as exc:  # interruptor roto: plantilla, que no cuesta nada
            log.warning("no se pudo leer explainer_enabled; se usa la plantilla (%s)", exc)
            return False

    async def explain(self, rationale: Mapping[str, Any]) -> str:
        """Devuelve la justificación. Nunca lanza: ante cualquier error cae a la plantilla."""
        rationale = dict(rationale or {})
        if not self.uses_llm():
            return _template_fallback(rationale)
        try:
            prompt = _build_prompt(rationale)
            # ``complete_json`` es síncrono (ver ``LLMClient``); se corre en un hilo aparte
            # para no bloquear el event loop mientras hace HTTP.
            response = await asyncio.to_thread(
                self._llm.complete_json, prompt, ExplainerResponse, system=SYSTEM_PROMPT
            )
        except Exception as exc:  # fail-open deliberado: cualquier error cae al template
            log.warning("ExplainerAgent cayó al template determinista: %s", exc)
            return _template_fallback(rationale)
        text = (response.text or "").strip()
        if not text:
            return _template_fallback(rationale)
        return text[: self._max_text_length]

    def explain_sync(self, rationale: Mapping[str, Any]) -> str:
        """Shim bloqueante para contextos que no pueden ``await`` (p. ej. un script sync)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Dentro de un loop en marcha no podemos usar ``run``; se corre en un hilo.
                return asyncio.run_coroutine_threadsafe(self.explain(rationale), loop).result()
        except RuntimeError:
            pass
        return asyncio.run(self.explain(rationale))


def explainer_from_env(
    env: Mapping[str, str] | None = None,
    *,
    llm: LLMClient | None = None,
    enabled: Callable[[], bool] | None = None,
) -> ExplainerAgent:
    """ExplainerAgent del worker.

    ``enabled`` por defecto lee ``EXPLAINER_ENABLED`` (``false`` si no está). ``llm`` es el
    cliente compartido con el NewsAgent; ``None`` (``LLM_PROVIDER=none``) = solo plantilla
    aunque el interruptor esté prendido.
    """
    if enabled is None:
        from tm_agents.orchestrator.settings import EnvSettingsProvider

        settings = EnvSettingsProvider(os.environ if env is None else env)
        enabled = lambda: settings.get_bool("explainer_enabled")  # noqa: E731
    if llm is None:
        log.info("ExplainerAgent sin LLM: se usa la plantilla determinista")
    return ExplainerAgent(llm, enabled=enabled)


# --- plantilla determinista -------------------------------------------------------------


def _template_fallback(rationale: Mapping[str, Any]) -> str:
    """Compone una justificación determinista con los campos disponibles en ``rationale``.

    Es tolerante a ``dict`` parciales o anidados: todas las claves que no existan se omiten
    o se reemplazan por valores por omisión. No consulta servicios externos, así que es la
    red de seguridad cuando el LLM no responde.
    """
    signals = list(rationale.get("signals") or [])
    total = len(signals)
    long_votes = sum(1 for s in signals if _signal_direction(s) == "long")
    action = _action_from_signals(signals, rationale)
    symbol = _symbol_from(signals, rationale)
    confidence = _confidence_from(signals, rationale)
    drawdown = _drawdown_from(rationale)

    parts: list[str] = []
    if total > 0:
        parts.append(
            f"{action} de {symbol} por voto del conjunto: {long_votes}/{total} agentes en largo."
        )
    else:
        parts.append(f"{action} de {symbol}.")
    if confidence is not None:
        parts.append(f"Confianza {confidence:.0%}.")
    if drawdown is not None:
        parts.append(f"Drawdown actual {drawdown:.1%}.")
    news = rationale.get("news_summary")
    if rationale.get("news_veto") and news:
        parts.append(f"Noticias: {news}.")
    return " ".join(parts)


def _build_prompt(rationale: Mapping[str, Any]) -> str:
    """Prompt para el LLM: números explícitos para que el modelo no los invente."""
    import json

    payload = _safe_json(rationale)
    return (
        "Redacta la justificación del siguiente rationale en 1-3 oraciones, en español, "
        "sin cambiar ningún número ni dirección (long/short/flat):\n\n"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
    )


def _safe_json(value: Any) -> Any:
    """Vuelve ``value`` serializable para el prompt (Pydantic -> dict, Decimal -> str)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {k: _safe_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_safe_json(v) for v in value]
    return value


def _signal_direction(signal: Any) -> str:
    direction = _get(signal, "direction")
    if direction is None:
        return ""
    if hasattr(direction, "value"):
        direction = direction.value
    return str(direction).lower()


def _action_from_signals(signals: list[Any], rationale: Mapping[str, Any]) -> str:
    """Resumen de la dirección: 'Compra', 'Venta' o 'Cambio neutral'."""
    direct = rationale.get("action")
    if isinstance(direct, str) and direct:
        return direct.capitalize()
    directions = {_signal_direction(s) for s in signals if _signal_direction(s)}
    if directions == {"long"}:
        return "Compra"
    if directions == {"short"}:
        return "Venta"
    if "long" in directions:
        return "Compra"
    if "short" in directions:
        return "Venta"
    return "Cambio neutral"


def _symbol_from(signals: list[Any], rationale: Mapping[str, Any]) -> str:
    direct = rationale.get("symbol")
    if isinstance(direct, str) and direct:
        return direct
    for s in signals:
        value = _get(s, "symbol")
        if isinstance(value, str) and value:
            return value
    return "el activo"


def _confidence_from(signals: list[Any], rationale: Mapping[str, Any]) -> float | None:
    """Confianza representativa: la de las señales que votan en la dirección dominante.

    Promediar sobre señales ``flat`` (confianza 0) ocultaría la convicción real; se toma
    el promedio solo de las que votan en la dirección final. Si el rationale trae
    ``confidence`` explícito, se respeta tal cual (no se recorta ni normaliza)."""
    direct = rationale.get("confidence")
    if isinstance(direct, int | float):
        return float(direct)
    dominant = _dominant_direction(signals)
    aligned = [s for s in signals if _signal_direction(s) == dominant] if dominant else signals
    confidences = [c for s in aligned if (c := _get(s, "confidence")) is not None]
    if not confidences:
        return None
    try:
        return float(sum(float(c) for c in confidences) / len(confidences))
    except (TypeError, ValueError):
        return None


def _dominant_direction(signals: list[Any]) -> str:
    """Dirección mayoritaria entre las señales (o ``""`` si no hay ninguna)."""
    counts: dict[str, int] = {}
    for s in signals:
        direction = _signal_direction(s)
        if direction and direction != "flat":
            counts[direction] = counts.get(direction, 0) + 1
    if not counts:
        return ""
    return max(counts, key=counts.get)


def _drawdown_from(rationale: Mapping[str, Any]) -> float | None:
    for key in ("drawdown", "current_drawdown", "max_drawdown"):
        value = rationale.get(key)
        if isinstance(value, int | float):
            return float(value)
    sim = rationale.get("sim")
    if sim is not None:
        value = _get(sim, "p_stop_loss")
        if isinstance(value, int | float):
            return float(value)
    return None


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


__all__ = ["ExplainerAgent", "ExplainerResponse", "explainer_from_env"]
