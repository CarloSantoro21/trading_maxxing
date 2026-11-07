"""Aplicación FastAPI del servicio api.

`create_app` permite inyectar la base, el bus de comandos y el verificador de JWT (así lo
hacen las pruebas). Lo que no se inyecta se construye en el `lifespan` a partir de las
variables de entorno: un `httpx.AsyncClient` contra PostgREST y un cliente de Redis.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis
from tm_shared.health import Health, health

from tm_api.auth import JWTVerifier
from tm_api.commands import CommandBus
from tm_api.db import Database, PostgrestClient
from tm_api.routers import backtests, bots, goals, portfolio, profiles, trades
from tm_api.settings import Settings

DESCRIPTION = """
API REST de **trading_maxxing**: perfiles de inversión, metas, bots (sleeves por
estrategia), portafolio, operaciones y backtests.

**Autenticación**: todas las rutas salvo `/health` exigen `Authorization: Bearer <jwt>`
con el `access_token` de Supabase Auth. La API reenvía ese token a PostgREST, así que
RLS decide qué filas ve y modifica cada usuario.

**Comandos**: arrancar, detener y el paro de emergencia publican en el stream de Redis
`tm:commands`; los backtests se encolan en `tm:backtests`.
"""

TAGS = [
    {"name": "profiles", "description": "Perfil de inversión (uno por usuario)."},
    {"name": "goals", "description": "Metas de rendimiento de un perfil."},
    {"name": "bots", "description": "Bots, arranque, paro y kill switch (`tm:commands`)."},
    {"name": "portfolio", "description": "Equity y posiciones por sleeve y en total."},
    {"name": "trades", "description": "Historial paginado de operaciones."},
    {"name": "backtests", "description": "Jobs de backtest (`tm:backtests`)."},
    {"name": "health", "description": "Estado del servicio."},
]


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    bus: CommandBus | None = None,
    verifier: JWTVerifier | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            app.state.settings = settings
            app.state.verifier = verifier or JWTVerifier(settings)
            if db is None:
                http = await stack.enter_async_context(httpx.AsyncClient(timeout=10.0))
                app.state.db = PostgrestClient(http, settings.rest_url, settings.supabase_anon_key)
            else:
                app.state.db = db
            if bus is None:
                redis = Redis.from_url(settings.redis_url)
                stack.push_async_callback(redis.aclose)
                app.state.bus = CommandBus(redis, settings)
            else:
                app.state.bus = bus
            yield

    app = FastAPI(
        title="trading_maxxing api",
        version="0.1.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    for module in (profiles, goals, bots, portfolio, trades, backtests):
        app.include_router(module.router)

    @app.get("/health", response_model=Health, tags=["health"])
    def get_health() -> Health:
        return health("api")

    return app


app = create_app()
