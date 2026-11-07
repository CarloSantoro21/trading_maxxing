from fastapi import FastAPI
from tm_shared.health import Health, health

app = FastAPI(title="trading_maxxing forecast")


@app.get("/health", response_model=Health)
def get_health() -> Health:
    return health("forecast")
