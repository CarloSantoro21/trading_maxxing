"""KronosForecastService: inferencia de Kronos con N trayectorias muestreadas.

El predictor de Kronos promedia internamente las `sample_count` muestras de cada serie. Para
conservar cada trayectoria por separado, replicamos la serie `samples` veces dentro de un solo
`predict_batch` con `sample_count=1`: el costo es el mismo (Kronos también replica el lote) y
cada fila del lote es una trayectoria independiente.

torch y el código de Kronos se importan de forma perezosa para que importar este módulo (y las
pruebas) no cargue pesos ni requiera red.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
REQUIRED_COLUMNS = ("timestamp", *OHLCV_COLUMNS)
MAX_CONTEXT = 512
MIN_CANDLES = 32
VALID_DEVICES = ("cpu", "cuda", "mps")


class Predictor(Protocol):
    """Interfaz mínima de `KronosPredictor` que usa el servicio (permite simularlo en pruebas)."""

    def predict_batch(
        self,
        df_list: list[pd.DataFrame],
        x_timestamp_list: list[pd.Series],
        y_timestamp_list: list[pd.Series],
        pred_len: int,
        T: float = 1.0,
        top_k: int = 0,
        top_p: float = 0.9,
        sample_count: int = 1,
        verbose: bool = True,
    ) -> list[pd.DataFrame]: ...


@dataclass(frozen=True)
class ForecastSettings:
    """Configuración del servicio, leída de variables de entorno."""

    model: str = "NeoQuasar/Kronos-small"
    tokenizer: str = "NeoQuasar/Kronos-Tokenizer-base"
    device: str | None = None
    max_context: int = MAX_CONTEXT
    max_batch: int = 512
    preload: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ForecastSettings:
        env = os.environ if env is None else env
        return cls(
            model=env.get("FORECAST_MODEL", cls.model),
            tokenizer=env.get("FORECAST_TOKENIZER", cls.tokenizer),
            device=env.get("FORECAST_DEVICE") or None,
            max_batch=int(env.get("FORECAST_MAX_BATCH", cls.max_batch)),
            preload=env.get("FORECAST_PRELOAD", "1").lower() not in ("0", "false", "no"),
        )


def _torch_backends() -> tuple[bool, bool]:
    """Disponibilidad de (mps, cuda) según torch."""
    import torch

    mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    return mps, torch.cuda.is_available()


def select_device(
    override: str | None = None,
    backends: Callable[[], tuple[bool, bool]] = _torch_backends,
) -> str:
    """Elige el dispositivo: `override` (FORECAST_DEVICE) si viene; si no, mps → cuda → cpu."""
    if override:
        device = override.strip().lower()
        if device.split(":")[0] not in VALID_DEVICES:
            raise ValueError(f"FORECAST_DEVICE inválido: {override!r} (usa cpu, cuda o mps)")
        return device
    mps, cuda = backends()
    if mps:
        return "mps"
    if cuda:
        return "cuda"
    return "cpu"


def validate_ohlcv(ohlcv: pd.DataFrame, min_candles: int = MIN_CANDLES) -> pd.DataFrame:
    """Verifica columnas y tamaño; devuelve una copia ordenada por tiempo y sin índice."""
    missing = [c for c in REQUIRED_COLUMNS if c not in ohlcv.columns]
    if missing:
        raise ValueError(f"Faltan columnas: {', '.join(missing)}")
    if len(ohlcv) < min_candles:
        raise ValueError(f"Se necesitan al menos {min_candles} velas, llegaron {len(ohlcv)}")
    df = ohlcv.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    if df["timestamp"].duplicated().any():
        raise ValueError("Hay timestamps repetidos")
    if df[list(OHLCV_COLUMNS)].isna().to_numpy().any():
        raise ValueError("Hay valores nulos en OHLCV")
    return df


def future_timestamps(timestamps: pd.Series, pred_len: int) -> pd.Series:
    """Timestamps de las velas futuras, con la frecuencia mediana de la historia."""
    step = timestamps.diff().dropna().median()
    last = timestamps.iloc[-1]
    return pd.Series([last + step * (i + 1) for i in range(pred_len)], name="timestamp")


@dataclass
class Forecast:
    """Resultado de una serie: `paths` tiene forma (samples, pred_len, 5) en orden OHLCV."""

    timestamps: pd.Series
    paths: np.ndarray

    @property
    def samples(self) -> int:
        return self.paths.shape[0]

    @property
    def pred_len(self) -> int:
        return self.paths.shape[1]


@dataclass
class KronosForecastService:
    """Envuelve `KronosPredictor`: carga el modelo una vez y genera trayectorias."""

    settings: ForecastSettings = field(default_factory=ForecastSettings.from_env)
    predictor: Predictor | None = None
    device: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self.device:
            self.device = select_device(self.settings.device)

    @property
    def loaded(self) -> bool:
        return self.predictor is not None

    def load_model(self, name: str | None = None, tokenizer: str | None = None) -> None:
        """Descarga (o lee de la caché de Hugging Face) modelo y tokenizer y crea el predictor."""
        with self._lock:
            if self.predictor is not None and name is None and tokenizer is None:
                return
            from tm_forecast.kronos import Kronos, KronosPredictor, KronosTokenizer

            tok = KronosTokenizer.from_pretrained(tokenizer or self.settings.tokenizer)
            model = Kronos.from_pretrained(name or self.settings.model)
            tok.eval()
            model.eval()
            self.predictor = KronosPredictor(
                model, tok, device=self.device, max_context=self.settings.max_context
            )

    def predict(
        self,
        ohlcv: pd.DataFrame,
        pred_len: int,
        samples: int = 100,
        seed: int | None = None,
        **sampling: Any,
    ) -> Forecast:
        """Pronostica `pred_len` velas con `samples` trayectorias para una serie."""
        return self.predict_batch([ohlcv], pred_len, samples, seed, **sampling)[0]

    def predict_batch(
        self,
        series: Sequence[pd.DataFrame],
        pred_len: int,
        samples: int = 100,
        seed: int | None = None,
        temperature: float = 1.0,
        top_p: float = 0.9,
        top_k: int = 0,
    ) -> list[Forecast]:
        """Pronostica varias series a la vez; todas deben tener la misma longitud de historia."""
        if pred_len < 1 or samples < 1:
            raise ValueError("pred_len y samples deben ser al menos 1")
        if not series:
            raise ValueError("No llegó ninguna serie")
        context = self.settings.max_context
        frames = [validate_ohlcv(df).tail(context).reset_index(drop=True) for df in series]
        if len({len(df) for df in frames}) != 1:
            raise ValueError("Todas las series deben tener la misma cantidad de velas")

        if self.predictor is None:
            self.load_model()
        if seed is not None:
            _seed_everything(seed)

        x_ts = [df["timestamp"] for df in frames]
        y_ts = [future_timestamps(ts, pred_len) for ts in x_ts]
        x_df = [df[list(OHLCV_COLUMNS)].astype("float64") for df in frames]

        # Cada serie se replica `samples` veces: una fila del lote por trayectoria
        rep = [i for i in range(len(frames)) for _ in range(samples)]
        preds: list[pd.DataFrame] = []
        for start in range(0, len(rep), self.settings.max_batch):
            chunk = rep[start : start + self.settings.max_batch]
            preds += self.predictor.predict_batch(  # type: ignore[union-attr]
                [x_df[i] for i in chunk],
                [x_ts[i] for i in chunk],
                [y_ts[i] for i in chunk],
                pred_len=pred_len,
                T=temperature,
                top_k=top_k,
                top_p=top_p,
                sample_count=1,
                verbose=False,
            )

        paths = np.stack([p[list(OHLCV_COLUMNS)].to_numpy(dtype=np.float64) for p in preds])
        paths = paths.reshape(len(frames), samples, pred_len, len(OHLCV_COLUMNS))
        return [Forecast(timestamps=y_ts[i], paths=paths[i]) for i in range(len(frames))]


def _seed_everything(seed: int) -> None:
    """Fija la semilla de torch (CPU, CUDA y MPS) y de numpy."""
    import torch

    torch.manual_seed(seed)
    np.random.seed(seed)
