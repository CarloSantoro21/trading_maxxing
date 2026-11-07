# forecast

Microservicio de pronóstico: envuelve Kronos-small y expone `POST /predict` con N trayectorias.

```bash
uv run --package tm-forecast uvicorn tm_forecast.main:app --reload --port 8001
```
