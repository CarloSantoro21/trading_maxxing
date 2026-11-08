"""Modelos Pydantic de request/response del servicio forecast."""

from datetime import datetime

from pydantic import BaseModel, Field
from tm_shared.health import Health

from tm_forecast.service import MAX_CONTEXT, MIN_CANDLES


class Candle(BaseModel):
    """Vela OHLCV de entrada. Todas las columnas son obligatorias (si falta alguna: 422)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ForecastCandle(Candle):
    """Vela pronosticada (mismas columnas que la de entrada)."""


class SamplingParams(BaseModel):
    pred_len: int = Field(24, ge=1, le=256, description="Velas a pronosticar")
    samples: int = Field(100, ge=1, le=500, description="Trayectorias muestreadas")
    seed: int | None = Field(None, description="Semilla para resultados reproducibles")
    temperature: float = Field(1.0, gt=0, le=2)
    top_p: float = Field(0.9, gt=0, le=1)


class PredictRequest(SamplingParams):
    candles: list[Candle] = Field(
        ...,
        min_length=MIN_CANDLES,
        max_length=MAX_CONTEXT,
        description=f"Historia OHLCV, de {MIN_CANDLES} a {MAX_CONTEXT} velas",
    )


class Series(BaseModel):
    candles: list[Candle] = Field(..., min_length=MIN_CANDLES, max_length=MAX_CONTEXT)


class PredictBatchRequest(SamplingParams):
    series: list[Series] = Field(..., min_length=1, max_length=16)


class PredictResponse(BaseModel):
    model: str
    device: str
    pred_len: int
    samples: int
    seed: int | None
    timestamps: list[datetime]
    trajectories: list[list[ForecastCandle]] = Field(
        ..., description="Matriz samples x pred_len de velas pronosticadas"
    )


class PredictBatchResponse(BaseModel):
    results: list[PredictResponse]


class ForecastHealth(Health):
    device: str
    model: str
    tokenizer: str
    model_loaded: bool
