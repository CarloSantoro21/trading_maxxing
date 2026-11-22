# trading_maxxing - diagrama de infraestructura (arquitectura final, 21-nov-2026).
#
# Todo lo público vive en Cloudflare, plan gratuito: Pages (frontend), Workers +
# Hono (API), D1 (base), Access (login Zero Trust) y Workers Observability (APM).
# El motor (agents, engine con NautilusTrader, forecast con Kronos y Redis) corre
# en la Mac de Carlo con docker compose, imágenes de Docker Hub y Watchtower, y
# solo hace conexiones SALIENTES.
#
# La librería `diagrams` no trae íconos por producto de Cloudflare: se usa el
# ícono genérico de Cloudflare con etiquetas claras, y genéricos/onprem para el
# resto. Para regenerar las PNG (requiere Graphviz: `brew install graphviz`):
#     pip install diagrams
#     cd docs/propuesta && python infra.py
#     cp img/infra.png ../wiki/img/infra.png
from diagrams import Cluster, Diagram, Edge
from diagrams.generic.database import SQL
from diagrams.onprem.client import Users
from diagrams.onprem.container import Docker
from diagrams.onprem.iac import Terraform
from diagrams.onprem.inmemory import Redis
from diagrams.onprem.network import Internet
from diagrams.programming.framework import Nextjs
from diagrams.programming.language import Python, Typescript
from diagrams.saas.cdn import Cloudflare

ga = {
    "fontsize": "22",
    "pad": "0.5",
    "splines": "spline",
    "nodesep": "0.8",
    "ranksep": "1.1",
}
na = {"fontsize": "13"}
ea = {"fontsize": "12"}

with Diagram(
    "trading_maxxing - Infraestructura (Cloudflare plan gratuito + motor en la Mac)",
    filename="img/infra",
    show=False,
    direction="TB",
    graph_attr=ga,
    node_attr=na,
    edge_attr=ea,
):
    users = Users("Equipo (< 10 personas)\nnavegador")

    with Cluster("Cloudflare - cuenta del proyecto (plan gratuito), por ambiente dev / prod"):
        access = Cloudflare("Cloudflare Access\n(Zero Trust: allowlist\n+ OTP por correo)")
        with Cluster("Pages: tm-web-dev (develop) / tm-web (main)"):
            pages = Nextjs("Next.js export estático\n/* -> archivos\n/api/* -> env.API")
        with Cluster("Workers: tm-api-dev / tm-api"):
            worker = Typescript("API Hono + TypeScript\n/api/* (usuarios)\n/internal/* (motor)")
        d1 = SQL("D1 (SQLite)\ntm-dev / tm-prod")
        obs = Cloudflare("Workers Observability\nlogs, trazas, errores,\nrequests y CPU time")
        svc = Cloudflare("Access app del motor\n(service token\nCF-Access-Client-Id/Secret)")

    with Cluster("Mac de Carlo - docker compose (solo conexiones salientes)"):
        with Cluster("motor"):
            agents = Python("agents\norquestador, estrategias,\nSimulationGate, RiskAgent")
            engine = Python("engine\nNautilusTrader\nTradingNode")
            forecast = Python("forecast\nKronos-small\n(PyTorch)")
            redis = Redis("Redis\ntm:signals / tm:decisions")
        watchtower = Docker("Watchtower\n(actualiza imágenes)")

    hub = Docker("Docker Hub\ntrading-maxxing-\n{agents,engine,forecast}")
    binance = Internet("Binance Spot Testnet\n(órdenes y velas)")
    llm = Internet("Noticias RSS / LLM\n(opcional, apagado\npor defecto)")
    tf = Terraform("trading_maxxing-infra\nTerraform cloudflare/cloudflare\nD1, Pages, Access")

    users >> Edge(label="HTTPS") >> access >> Edge(label="cookie CF_Authorization") >> pages
    pages >> Edge(label="service binding API\n(mismo origen)") >> worker
    worker >> Edge(label="SQL") >> d1
    worker >> Edge(style="dotted", label="logs y trazas") >> obs

    agents >> Edge(label="HTTPS saliente\nX-Service-Token\n+ service token") >> svc
    engine >> Edge(label="trades, equity,\nheartbeat") >> svc
    svc >> Edge(label="/internal/*") >> worker

    agents >> Edge(label="señales") >> redis
    redis >> Edge(label="decisiones") >> engine
    agents >> Edge(label="/predict") >> forecast
    engine >> Edge(label="órdenes bracket") >> binance
    agents >> Edge(style="dashed", label="NEWS_ENABLED /\nLLM_PROVIDER") >> llm

    hub >> Edge(style="dotted", label="pull") >> watchtower
    watchtower >> Edge(style="dotted", label="reinicia") >> agents

    tf >> Edge(style="dashed", label="crea") >> access
    tf >> Edge(style="dashed", label="crea") >> d1
