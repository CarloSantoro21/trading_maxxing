"""Benchmark del KV cache de Kronos (issue #62).

Mide tiempo total para producir N muestras en CPU y, si está disponible, en
`mps`, con `use_cache=True` vs `use_cache=False`. Usa los pesos reales de
`NeoQuasar/Kronos-small` + `NeoQuasar/Kronos-Tokenizer-base` (se descargan a
`~/.cache/huggingface` la primera vez).

Ejecución típica:

    uv run python services/forecast/scripts/bench_kronos_cache.py \\
        --context 400 --pred-len 24 --trajectories 10 25 50

Opciones útiles: `--warmup 1` para no contar la primera corrida (caches tibios)
y `--devices cpu` para saltarse MPS.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Iterable

import numpy as np
import torch
from tm_forecast.kronos import Kronos, KronosTokenizer
from tm_forecast.kronos.kronos import auto_regressive_inference


def _build_tensors(
    context: int, pred_len: int, device: str
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 1, context))
    x = np.stack(
        [close, close + 1, close - 1, close, np.full(context, 10.0), np.full(context, 1000.0)],
        axis=1,
    ).astype(np.float32)
    x = (x - x.mean(0)) / (x.std(0) + 1e-5)
    x = np.clip(x, -5, 5)[np.newaxis, :]
    stamp_x = np.zeros((1, context, 5), dtype=np.float32)
    stamp_y = np.zeros((1, pred_len, 5), dtype=np.float32)
    for seq, length in ((stamp_x, context), (stamp_y, pred_len)):
        for t in range(length):
            seq[0, t, 0] = (t * 15) % 60
            seq[0, t, 1] = (t // 4) % 24
            seq[0, t, 2] = (t // 96) % 7
            seq[0, t, 3] = ((t // 96) % 28) + 1
            seq[0, t, 4] = ((t // 2688) % 12) + 1
    return (
        torch.from_numpy(x).to(device),
        torch.from_numpy(stamp_x).to(device),
        torch.from_numpy(stamp_y).to(device),
    )


def _bench_once(
    tokenizer,
    model,
    x: torch.Tensor,
    stamp_x: torch.Tensor,
    stamp_y: torch.Tensor,
    max_context: int,
    pred_len: int,
    samples: int,
    use_cache: bool,
) -> float:
    if x.device.type in ("cuda", "mps"):
        torch.mps.synchronize() if x.device.type == "mps" else torch.cuda.synchronize()
    start = time.perf_counter()
    auto_regressive_inference(
        tokenizer,
        model,
        x,
        stamp_x,
        stamp_y,
        max_context=max_context,
        pred_len=pred_len,
        clip=5,
        T=1.0,
        top_k=0,
        top_p=0.9,
        sample_count=samples,
        verbose=False,
        use_cache=use_cache,
    )
    if x.device.type in ("cuda", "mps"):
        torch.mps.synchronize() if x.device.type == "mps" else torch.cuda.synchronize()
    return time.perf_counter() - start


def run(
    devices: Iterable[str],
    trajectories: list[int],
    context: int,
    pred_len: int,
    repeats: int,
    warmup: int,
    max_context: int,
) -> None:
    print(
        f"Kronos KV cache bench · context={context} velas, pred_len={pred_len}, "
        f"max_context={max_context}"
    )
    print(f"PyTorch {torch.__version__}")

    rows: list[tuple[str, int, bool, float, float]] = []
    for device in devices:
        print(f"\n=== device: {device} ===")
        tokenizer = (
            KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base").to(device).eval()
        )
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small").to(device).eval()
        x, stamp_x, stamp_y = _build_tensors(context, pred_len, device)

        for samples in trajectories:
            for use_cache in (False, True):
                for _ in range(warmup):
                    _bench_once(
                        tokenizer,
                        model,
                        x,
                        stamp_x,
                        stamp_y,
                        max_context,
                        pred_len,
                        samples,
                        use_cache,
                    )
                times = [
                    _bench_once(
                        tokenizer,
                        model,
                        x,
                        stamp_x,
                        stamp_y,
                        max_context,
                        pred_len,
                        samples,
                        use_cache,
                    )
                    for _ in range(repeats)
                ]
                median = statistics.median(times)
                best = min(times)
                tag = "cache ON " if use_cache else "cache OFF"
                print(
                    f"  samples={samples:>3}  {tag}  "
                    f"median={median:6.2f}s  best={best:6.2f}s  (n={repeats})"
                )
                rows.append((device, samples, use_cache, median, best))

    print("\nResumen:")
    print(f"  {'device':<6} {'samples':>7} {'mode':<10} {'median s':>9} {'best s':>8}")
    for device, samples, use_cache, median, best in rows:
        mode = "cache ON" if use_cache else "cache OFF"
        print(f"  {device:<6} {samples:>7d} {mode:<10} {median:>9.2f} {best:>8.2f}")


def _default_devices() -> list[str]:
    devs = ["cpu"]
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        devs.append("mps")
    return devs


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--context", type=int, default=400, help="Velas de contexto")
    p.add_argument("--pred-len", type=int, default=24, help="Pasos autorregresivos")
    p.add_argument(
        "--max-context",
        type=int,
        default=512,
        help="Ventana del modelo (Kronos-small usa 512)",
    )
    p.add_argument(
        "--trajectories",
        type=int,
        nargs="+",
        default=[10, 25, 50],
        help="Lista de tamaños de muestras a medir",
    )
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument(
        "--devices",
        type=str,
        nargs="+",
        default=None,
        help="Dispositivos a usar (default: cpu y mps si están disponibles)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    devices = args.devices if args.devices is not None else _default_devices()
    run(
        devices=devices,
        trajectories=args.trajectories,
        context=args.context,
        pred_len=args.pred_len,
        repeats=args.repeats,
        warmup=args.warmup,
        max_context=args.max_context,
    )


if __name__ == "__main__":
    main()
