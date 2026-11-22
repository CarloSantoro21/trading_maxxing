"""ExplainerAgent (#37): justificación en lenguaje natural con fallback determinista.

El issue es explícito: la explicación NUNCA altera los números y NO puede detener la
decisión. Estas pruebas blindan ambas invariantes.

Se usa ``asyncio.run`` directamente en vez de ``@pytest.mark.asyncio`` para no depender
de ``pytest-asyncio``: los tests siguen el mismo estilo que ``tests/test_llm.py``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tm_agents.explainer import ExplainerAgent, ExplainerResponse, _template_fallback
from tm_agents.llm.base import LLMClient, LLMError
from tm_shared.contracts import (
    Direction,
    Horizon,
    Rationale,
    RiskVerdict,
    Signal,
    SimResult,
    Timeframe,
)

# --- helpers --------------------------------------------------------------------------


class _RecordingLLM(LLMClient):
    """LLM simulado que devuelve una respuesta fija y guarda los prompts."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.prompts: list[str] = []

    def complete_json(self, prompt, schema, *, system=None):
        self.prompts.append(prompt)
        return schema.model_validate({"text": self._text})


class _FailingLLM(LLMClient):
    """LLM que siempre lanza (fail-open ⇒ se usa la plantilla determinista)."""

    def complete_json(self, prompt, schema, *, system=None):
        raise LLMError("groq caído")


class _SlowLLM(LLMClient):
    """LLM que tarda ``delay`` segundos (para probar el fire-and-forget)."""

    def __init__(self, text: str, delay: float) -> None:
        self._text = text
        self._delay = delay
        self.calls = 0

    def complete_json(self, prompt, schema, *, system=None):
        self.calls += 1
        time.sleep(self._delay)
        return schema.model_validate({"text": self._text})


def _rationale_dict(
    *,
    confidence: float = 0.75,
    long_votes: int = 3,
    total: int = 4,
    symbol: str = "BTCUSDT",
) -> dict:
    """Construye un ``Rationale`` Pydantic como el BotRunner y lo serializa a dict."""
    assert 0 <= long_votes <= total
    now = datetime(2026, 3, 1, tzinfo=UTC)
    signals = [
        Signal(
            symbol=symbol,
            direction=Direction.LONG if i < long_votes else Direction.FLAT,
            confidence=confidence if i < long_votes else 0.0,
            horizon=Horizon.MEDIUM,
            timeframe=Timeframe.D1,
            source=f"agent_{i}",
            generated_at=now,
        )
        for i in range(total)
    ]
    rationale = Rationale(
        signals=signals,
        weights={"trend": 0.4, "exposure": 0.6, "target_fraction": 0.75},
        news_veto=False,
        risk=RiskVerdict(approved=True, reasons=["ok"]),
        sim=SimResult(
            p_take_profit=0.6,
            p_stop_loss=0.1,
            p_open=0.3,
            expected_value=0.4,
            n_paths=1000,
            source="bootstrap",
        ),
        text=f"trend long con confianza {confidence:.2f}",
    )
    return rationale.model_dump(mode="json")


# --- explain(): camino feliz y fallbacks ----------------------------------------------


def test_explain_uses_llm_text():
    llm = _RecordingLLM("Compra de BTCUSDT con 3/4 agentes a favor.")
    agent = ExplainerAgent(llm)
    out = asyncio.run(agent.explain(_rationale_dict()))
    assert out == "Compra de BTCUSDT con 3/4 agentes a favor."
    assert len(llm.prompts) == 1 and "BTCUSDT" in llm.prompts[0]


def test_explain_fallback_on_llm_error():
    agent = ExplainerAgent(_FailingLLM())
    out = asyncio.run(
        agent.explain(_rationale_dict(confidence=0.5, long_votes=3, total=4, symbol="ETHUSDT"))
    )
    # No re-raise: devuelve la plantilla determinista.
    assert "Compra" in out and "ETHUSDT" in out
    assert "3/4 agentes en largo" in out
    assert "Confianza 50%" in out  # exactamente como viene en el rationale


def test_explain_fallback_on_arbitrary_exception():
    class _Boom(LLMClient):
        def complete_json(self, prompt, schema, *, system=None):
            raise RuntimeError("pánico inesperado")

    out = asyncio.run(ExplainerAgent(_Boom()).explain(_rationale_dict()))
    assert "Compra" in out  # fallback, sin propagar


def test_explain_without_llm_returns_template():
    """Sin LLM configurado se usa directamente la plantilla."""
    out = asyncio.run(ExplainerAgent(None).explain(_rationale_dict(long_votes=2, total=3)))
    assert "2/3 agentes en largo" in out


def test_explain_empty_text_falls_back():
    out = asyncio.run(ExplainerAgent(_RecordingLLM("   ")).explain(_rationale_dict()))
    # Si el LLM devuelve vacío, cae al template.
    assert out.startswith("Compra de BTCUSDT")


def test_explain_truncates_long_text():
    very_long = "A" * 5000
    agent = ExplainerAgent(_RecordingLLM(very_long), max_text_length=120)
    out = asyncio.run(agent.explain(_rationale_dict()))
    assert len(out) == 120


# --- plantilla determinista: los números NO se mueven ---------------------------------


def test_template_numbers_match_rationale():
    rationale = _rationale_dict(confidence=0.42, long_votes=2, total=5, symbol="BTCUSDT")
    out = _template_fallback(rationale)
    # La narrativa reproduce EXACTAMENTE los números del rationale.
    assert "2/5 agentes en largo" in out
    assert "Confianza 42%" in out
    assert "Compra de BTCUSDT" in out


def test_template_handles_missing_fields_defensively():
    # Rationale vacío: no debe lanzar y debe decir algo razonable.
    out = _template_fallback({})
    assert "el activo" in out  # placeholder cuando no sabemos el símbolo


def test_template_without_signals_uses_zero_votes():
    out = _template_fallback({"symbol": "BTCUSDT"})
    assert "BTCUSDT" in out
    # Sin señales no se inventan votos.
    assert "agentes en largo" not in out


def test_template_short_direction():
    rationale = Rationale(
        signals=[
            Signal(
                symbol="BTCUSDT",
                direction=Direction.SHORT,
                confidence=0.6,
                horizon=Horizon.MEDIUM,
                timeframe=Timeframe.D1,
                source="x",
                generated_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        ]
    ).model_dump(mode="json")
    out = _template_fallback(rationale)
    assert "Venta" in out


def test_template_news_veto_included():
    out = _template_fallback(
        {"symbol": "BTCUSDT", "signals": [], "news_veto": True, "news_summary": "hackeo"}
    )
    assert "Noticias: hackeo" in out


def test_template_formats_confidence_literally():
    """La plantilla formatea la confianza sin modificarla (no la recorta a [0,1])."""
    out = _template_fallback({"symbol": "BTCUSDT", "confidence": 1.234, "signals": []})
    assert "123%" in out  # 1.234 -> 123%


# --- explain_sync: shim bloqueante ----------------------------------------------------


def test_explain_sync_without_loop():
    agent = ExplainerAgent(_RecordingLLM("ok"))
    assert agent.explain_sync(_rationale_dict()) == "ok"


def test_explain_sync_fallback():
    agent = ExplainerAgent(_FailingLLM())
    out = agent.explain_sync(_rationale_dict())
    assert "Compra" in out  # plantilla


# --- invariantes clave ----------------------------------------------------------------


def test_explain_never_modifies_rationale():
    """La explicación NUNCA altera números: el dict de entrada queda idéntico."""
    rationale = _rationale_dict()
    before = rationale.copy()
    asyncio.run(ExplainerAgent(_RecordingLLM("algo")).explain(rationale))
    asyncio.run(ExplainerAgent(_FailingLLM()).explain(rationale))
    assert rationale == before


def test_explainer_response_schema_rejects_empty():
    with pytest.raises(ValidationError):
        ExplainerResponse.model_validate({"text": ""})


# --- fire-and-forget en el BotRunner --------------------------------------------------


def _build_runner_with_explainer(make_bot, explainer, explanation_sink):
    from tm_agents.orchestrator.bot import BotRunner

    runner, sink, shadow = make_bot()
    runner = BotRunner(
        config=runner.config,
        sink=sink,
        shadow_sink=runner.shadow_sink,
        allocator=runner.allocator,
        limits=runner.limits,
        explainer=explainer,
        explanation_sink=explanation_sink,
    )
    return runner, sink, shadow


def test_bot_fire_and_forget_does_not_block(make_bot, make_event):
    """Un LLM lento no debe bloquear a ``on_event``: la decisión sale enseguida."""
    recorded: dict[str, str] = {}
    slow = _SlowLLM("explicación tardía", delay=0.3)
    runner, sink, _ = _build_runner_with_explainer(
        make_bot,
        ExplainerAgent(slow),
        lambda env_id, text: recorded.__setitem__(env_id, text),
    )

    async def exercise():
        start = time.monotonic()
        envelopes = runner.on_event(make_event(1.0))
        elapsed = time.monotonic() - start
        return envelopes, elapsed

    envelopes, elapsed = asyncio.run(_wait_for_tasks(runner, exercise))

    # La decisión salió sin esperar al LLM (margen amplio: 100 ms < 300 ms).
    assert elapsed < 0.1, f"on_event bloqueó {elapsed:.3f}s por el LLM"
    assert envelopes and envelopes[0] in sink.items
    assert recorded.get(envelopes[0].id) == "explicación tardía"


def test_bot_explainer_failure_does_not_touch_decision(make_bot, make_event):
    """Un explainer que lanza NO cambia la decisión ya publicada."""
    runner, sink, _ = _build_runner_with_explainer(
        make_bot, ExplainerAgent(_FailingLLM()), lambda env_id, text: None
    )

    async def exercise():
        envelopes = runner.on_event(make_event(1.0))
        return envelopes, 0.0

    envelopes, _ = asyncio.run(_wait_for_tasks(runner, exercise))
    assert envelopes
    envelope = envelopes[0]

    # La decisión en el sink no se tocó: mismos números exactos.
    assert sink.items[0].decision.quantity == envelope.decision.quantity
    assert sink.items[0].decision.entry_price == envelope.decision.entry_price


def test_bot_explanation_sink_exception_is_swallowed(make_bot, make_event):
    """Un sink que lanza no detiene al runner ni altera la decisión."""

    def crashing_sink(env_id, text):
        raise RuntimeError("API caída")

    runner, sink, _ = _build_runner_with_explainer(
        make_bot, ExplainerAgent(_RecordingLLM("ok")), crashing_sink
    )

    async def exercise():
        envelopes = runner.on_event(make_event(1.0))
        return envelopes, 0.0

    envelopes, _ = asyncio.run(_wait_for_tasks(runner, exercise))
    assert envelopes and sink.items  # la decisión vive aunque el sink haya tronado


def test_bot_without_loop_skips_explainer(make_bot, make_event):
    """Sin event loop (p. ej. en ``run_offline``) el explainer no se programa."""
    called: list[str] = []

    class _Boom(LLMClient):
        def complete_json(self, prompt, schema, *, system=None):
            called.append("x")
            return schema.model_validate({"text": "x"})

    runner, _sink, _ = _build_runner_with_explainer(
        make_bot, ExplainerAgent(_Boom()), lambda env_id, text: None
    )
    envelopes = runner.on_event(make_event(1.0))
    assert envelopes  # la decisión salió
    assert called == []  # el explainer no se invocó (no hay loop corriendo)
    assert runner.explain_tasks == []


def test_bot_without_explainer_keeps_working(make_bot, make_event):
    """Compatibilidad hacia atrás: sin explainer el BotRunner opera igual que antes."""
    runner, sink, _ = make_bot()
    envelopes = runner.on_event(make_event(1.0))
    assert envelopes and sink.items
    assert runner.explain_tasks == []


# --- utilidades -----------------------------------------------------------------------


async def _wait_for_tasks(runner, coro_factory):
    """Ejecuta ``coro_factory`` dentro de un loop y espera a todas las tareas del explainer."""
    out = await coro_factory()
    # Snapshot tras la ejecución porque las tareas se agregan dentro de ``on_event``.
    tasks = list(runner.explain_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return out
