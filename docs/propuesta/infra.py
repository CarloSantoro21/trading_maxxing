# trading_maxxing - diagrama de infraestructura (Azure + Cloudflare).
#
# Pendiente (#7): este script reemplaza al anterior, que dibujaba AWS. Para
# regenerar la PNG:
#     pip install diagrams  (requiere Graphviz)
#     cd docs/propuesta && python infra.py
#
# Iconos: https://learn.microsoft.com/azure/architecture/icons/
from diagrams import Diagram, Cluster, Edge
from diagrams.azure.compute import AppServices
from diagrams.azure.database import CacheForRedis
from diagrams.azure.devops import ApplicationInsights
from diagrams.azure.analytics import LogAnalyticsWorkspaces
from diagrams.onprem.client import Users
from diagrams.onprem.database import Postgresql
from diagrams.onprem.container import Docker
from diagrams.onprem.network import Cloudflare, Internet
from diagrams.programming.framework import Nextjs, Fastapi
from diagrams.programming.language import Python

ga = {"fontsize": "20", "pad": "0.4", "splines": "spline", "nodesep": "0.7", "ranksep": "1.1"}
with Diagram(
    "trading_maxxing - Infraestructura (Azure + Cloudflare)",
    filename="img/infra",
    show=False,
    direction="TB",
    graph_attr=ga,
):
    users = Users("Inversionistas")
    cf = Cloudflare("Cloudflare Pages\n(tm-web-dev, tm-web)")
    web = Nextjs("Next.js\nexport estatico")
    hub = Docker("Docker Hub\n(imagenes)")

    with Cluster("Azure - Resource Group rg-tm-<env>"):
        with Cluster("App Service Plan Linux B1"):
            api = Fastapi("tm-api-<env>\nFastAPI")
            agents = Python("tm-agents-<env>\norquestador + engine\n+ forecast")
        redis = CacheForRedis("Azure Cache for Redis\nBasic C0")
        appi = ApplicationInsights("Application Insights")
        logs = LogAnalyticsWorkspaces("Log Analytics")

    with Cluster("Servicios externos"):
        supa = Postgresql("Supabase Cloud\nPostgres + Auth + Realtime")
        ext = Internet("Binance Testnet /\nNews API / LLM API")

    users >> cf >> web
    web >> Edge(label="NEXT_PUBLIC_API_URL") >> api
    api >> Edge(style="dashed", label="Auth/RLS") >> supa
    api >> Edge(label="jobs") >> redis
    redis >> agents
    agents >> Edge(label="decisiones") >> redis
    agents >> Edge(label="ordenes / datos") >> ext
    agents >> Edge(label="noticias / LLM") >> ext
    agents >> supa
    api >> Edge(style="dotted", label="traces") >> appi
    agents >> Edge(style="dotted", label="traces") >> appi
    appi >> logs
    hub >> Edge(style="dotted", label="pull imagen") >> api
    hub >> Edge(style="dotted", label="pull imagen") >> agents
