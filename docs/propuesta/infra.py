from diagrams import Diagram, Cluster, Edge
from diagrams.aws.network import Route53, ELB, VPC
from diagrams.aws.compute import ECS, Fargate
from diagrams.aws.database import ElasticacheForRedis
from diagrams.aws.security import SecretsManager
from diagrams.aws.management import Cloudwatch
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.onprem.database import Postgresql
from diagrams.onprem.container import Docker
from diagrams.onprem.network import Internet
from diagrams.programming.framework import Nextjs, Fastapi
from diagrams.programming.language import Python

ga = {"fontsize":"20","pad":"0.4","splines":"spline","nodesep":"0.7","ranksep":"1.1"}
with Diagram("trading_maxxing - Infraestructura (AWS)", filename="img/infra", show=False, direction="TB", graph_attr=ga):
    users = Users("Inversionistas")
    dns = Route53("Route 53")
    hub = Docker("Docker Hub\n(imagenes)")
    with Cluster("AWS us-east-1"):
        with Cluster("VPC"):
            alb = ELB("Application\nLoad Balancer")
            with Cluster("ECS Cluster (Fargate)"):
                web = Nextjs("web\nNext.js")
                api = Fastapi("api\nFastAPI")
                orch = Python("agents\norquestador")
                fc = Python("forecast\nKronos")
                eng = Python("engine\nNautilus Trader")
            redis = ElasticacheForRedis("ElastiCache Redis\nbus + cache")
        secrets = SecretsManager("Secrets Manager")
        logs = Cloudwatch("CloudWatch\nlogs/metrics")
        s3 = S3("S3\nmodelos + reportes")
    with Cluster("Servicios externos"):
        supa = Postgresql("Supabase\nPostgres + Auth\n+ Realtime")
        ext = Internet("Binance Testnet /\nNews API / LLM API")
    users >> dns >> alb
    alb >> web
    alb >> api
    web >> Edge(style="dashed", label="Auth/Realtime") >> supa
    api >> supa
    api >> Edge(label="jobs") >> redis
    redis >> orch
    orch >> Edge(label="forecast") >> fc
    orch >> Edge(label="decisiones") >> redis
    redis >> eng
    eng >> Edge(label="ordenes / datos") >> ext
    orch >> Edge(label="noticias") >> ext
    eng >> supa
    fc >> s3
    hub >> Edge(style="dotted", label="pull imagenes") >> alb
    api >> Edge(style="dotted", label="secrets/logs") >> secrets
    secrets - Edge(style="invis") - logs
