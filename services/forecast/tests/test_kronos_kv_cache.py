"""Equivalencia KV cache vs camino original en Kronos (issue #62).

Se construye una versión pequeña de `KronosTokenizer` + `Kronos` con pesos
aleatorios (sin descargar de Hugging Face) y se corre `auto_regressive_inference`
con `use_cache=True` y `use_cache=False`. Con la misma semilla las dos rutas
deben producir la misma matriz de predicciones; el cache solo cambia qué se
recomputa, no el orden en el que se muestrea.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
from tm_forecast.kronos.kronos import Kronos, KronosTokenizer, auto_regressive_inference


def _build_tokenizer() -> KronosTokenizer:
    return KronosTokenizer(
        d_in=6,
        d_model=32,
        n_heads=4,
        ff_dim=64,
        n_enc_layers=2,
        n_dec_layers=2,
        ffn_dropout_p=0.0,
        attn_dropout_p=0.0,
        resid_dropout_p=0.0,
        s1_bits=4,
        s2_bits=4,
        beta=0.25,
        gamma0=1.0,
        gamma=1.0,
        zeta=1.0,
        group_size=2,
    )


def _build_model() -> Kronos:
    return Kronos(
        s1_bits=4,
        s2_bits=4,
        n_layers=2,
        d_model=32,
        n_heads=4,
        ff_dim=64,
        ffn_dropout_p=0.0,
        attn_dropout_p=0.0,
        resid_dropout_p=0.0,
        token_dropout_p=0.0,
        learn_te=True,
    )


def _make_inputs(seed: int, ctx: int, horizon: int):
    rng = np.random.default_rng(seed)
    x = torch.from_numpy(rng.normal(0, 1, size=(1, ctx, 6)).astype(np.float32))
    # Timestamps en formato [minute, hour, weekday, day, month] con valores plausibles.
    x_stamp = torch.zeros(1, ctx, 5, dtype=torch.float32)
    y_stamp = torch.zeros(1, horizon, 5, dtype=torch.float32)
    for seq, length in ((x_stamp, ctx), (y_stamp, horizon)):
        for t in range(length):
            seq[0, t, 0] = (t * 15) % 60   # minute
            seq[0, t, 1] = (t // 4) % 24   # hour
            seq[0, t, 2] = (t // 96) % 7   # weekday
            seq[0, t, 3] = ((t // 96) % 28) + 1  # day
            seq[0, t, 4] = ((t // 2688) % 12) + 1  # month
    return x, x_stamp, y_stamp


@pytest.mark.parametrize("sample_count", [1, 3])
def test_kv_cache_matches_reference(sample_count: int) -> None:
    torch.manual_seed(0)
    tokenizer = _build_tokenizer().eval()
    model = _build_model().eval()

    ctx, horizon, max_context = 32, 4, 64
    x, x_stamp, y_stamp = _make_inputs(seed=1234, ctx=ctx, horizon=horizon)

    kwargs = dict(
        max_context=max_context,
        pred_len=horizon,
        clip=5,
        T=1.0,
        top_k=0,
        top_p=0.99,
        sample_count=sample_count,
        verbose=False,
    )

    torch.manual_seed(42)
    preds_cached = auto_regressive_inference(
        tokenizer, model, x, x_stamp, y_stamp, use_cache=True, **kwargs
    )

    torch.manual_seed(42)
    preds_uncached = auto_regressive_inference(
        tokenizer, model, x, x_stamp, y_stamp, use_cache=False, **kwargs
    )

    # Con pesos y semilla fijos y sin dropout las dos rutas son matemáticamente
    # equivalentes; permitimos un epsilon mínimo por fusiones de SDPA.
    assert preds_cached.shape == preds_uncached.shape
    np.testing.assert_allclose(preds_cached, preds_uncached, atol=1e-5, rtol=1e-5)


def test_kv_cache_rolling_window_matches_reference() -> None:
    """Fuerza la rotación de la ventana (contexto + horizon > max_context)."""
    torch.manual_seed(0)
    tokenizer = _build_tokenizer().eval()
    model = _build_model().eval()

    ctx, horizon, max_context = 24, 6, 28
    x, x_stamp, y_stamp = _make_inputs(seed=99, ctx=ctx, horizon=horizon)

    kwargs = dict(
        max_context=max_context,
        pred_len=horizon,
        clip=5,
        T=1.0,
        top_k=0,
        top_p=0.99,
        sample_count=2,
        verbose=False,
    )

    torch.manual_seed(7)
    preds_cached = auto_regressive_inference(
        tokenizer, model, x, x_stamp, y_stamp, use_cache=True, **kwargs
    )

    torch.manual_seed(7)
    preds_uncached = auto_regressive_inference(
        tokenizer, model, x, x_stamp, y_stamp, use_cache=False, **kwargs
    )

    np.testing.assert_allclose(preds_cached, preds_uncached, atol=1e-5, rtol=1e-5)
