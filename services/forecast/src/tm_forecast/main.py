"""Servicio forecast de trading_maxxing: Kronos detrás de FastAPI."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from tm_shared.health import health

from tm_forecast.schemas import (
    Candle,
    ForecastCandle,
    ForecastHealth,
    PredictBatchRequest,
    PredictBatchResponse,
    PredictRequest,
    PredictResponse,
    SamplingParams,
)
from tm_forecast.service import OHLCV_COLUMNS, Forecast, KronosForecastService


def _to_frame(candles: list[Candle]) -> pd.DataFrame:
    return pd.DataFrame([c.model_dump() for c in candles])


def _to_response(svc: KronosForecastService, f: Forecast, p: SamplingParams) -> PredictResponse:
    stamps = [ts.to_pydatetime() for ts in f.timestamps]
    trajectories = [
        [
            ForecastCandle(
                timestamp=stamps[t], **dict(zip(OHLCV_COLUMNS, row.tolist(), strict=True))
            )
            for t, row in enumerate(path)
        ]
        for path in f.paths
    ]
    return PredictResponse(
        model=svc.settings.model,
        device=svc.device,
        pred_len=f.pred_len,
        samples=f.samples,
        seed=p.seed,
        timestamps=stamps,
        trajectories=trajectories,
    )


def create_app(service: KronosForecastService | None = None) -> FastAPI:
    """Crea la app. Se puede inyectar un servicio (con predictor simulado en pruebas).

    El servicio se crea sin cargar pesos; el `lifespan` lo carga una sola vez al arranque
    (salvo `FORECAST_PRELOAD=0`). Si no se precargó, la primera petición lo carga.
    """
    svc = service or KronosForecastService()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if svc.settings.preload and not svc.loaded:
            await run_in_threadpool(svc.load_model)
        yield

    app = FastAPI(title="trading_maxxing forecast", lifespan=lifespan)
    app.state.forecast = svc

    def _service(request: Request) -> KronosForecastService:
        return request.app.state.forecast

    async def _run(svc: KronosForecastService, frames: list[pd.DataFrame], p: SamplingParams):
        try:
            return await run_in_threadpool(
                svc.predict_batch,
                frames,
                pred_len=p.pred_len,
                samples=p.samples,
                seed=p.seed,
                temperature=p.temperature,
                top_p=p.top_p,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/health", response_model=ForecastHealth)
    def get_health(request: Request) -> ForecastHealth:
        s = _service(request)
        return ForecastHealth(
            **health("forecast").model_dump(),
            device=s.device,
            model=s.settings.model,
            tokenizer=s.settings.tokenizer,
            model_loaded=s.loaded,
        )

    @app.post("/predict", response_model=PredictResponse)
    async def predict(body: PredictRequest, request: Request) -> PredictResponse:
        s = _service(request)
        [forecast] = await _run(s, [_to_frame(body.candles)], body)
        return _to_response(s, forecast, body)

    @app.post("/predict/batch", response_model=PredictBatchResponse)
    async def predict_batch(body: PredictBatchRequest, request: Request) -> PredictBatchResponse:
        s = _service(request)
        forecasts = await _run(s, [_to_frame(x.candles) for x in body.series], body)
        return PredictBatchResponse(results=[_to_response(s, f, body) for f in forecasts])

    return app


app = create_app()
