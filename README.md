# trading_maxxing

Proyecto final de Infraestructura para el Desarrollo Continuo (ITESO).

Plataforma multiestrategia de trading de criptomonedas: un comité de agentes combina los pronósticos de [Kronos](https://github.com/shiyu-coder/Kronos) con estrategias cuantitativas con evidencia publicada en cripto (trend following y momentum intradía). Cada estrategia se valida con backtest in-sample/out-of-sample y Monte Carlo, cada operación se simula antes de ejecutarse y todo corre sobre [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) en Binance Testnet (paper trading).

- Propuesta: [docs/propuesta/Propuesta-trading_maxxing.md](docs/propuesta/Propuesta-trading_maxxing.md) ([PDF](docs/propuesta/Propuesta-trading_maxxing.pdf))
- Plan de trabajo: [Project Board](https://github.com/users/CarloSantoro21/projects/2) e [Issues](https://github.com/CarloSantoro21/trading_maxxing/issues)
- Stack: Next.js, FastAPI, Supabase, Redis, Docker y Docker Hub, GitHub Actions, Terraform, AWS ECS Fargate

Proyecto académico. Opera solo en entornos de prueba y no es asesoría financiera.
