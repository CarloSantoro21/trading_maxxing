# trading_maxxing - diagrama de deployment y CI/CD (arquitectura final, 21-nov-2026).
#
# GitHub Actions: CI en cada PR; merge a develop -> ambiente dev automático;
# merge a main -> environment `prod` con aprobación (required reviewers).
# Backend = Worker (wrangler deploy + migraciones de D1) + imágenes del motor en
# Docker Hub (Watchtower las baja en la Mac). Frontend = wrangler pages deploy.
#
# La librería `diagrams` no trae íconos por producto de Cloudflare: se usa el
# ícono genérico de Cloudflare con etiquetas claras. Para regenerar las PNG
# (requiere Graphviz: `brew install graphviz`):
#     pip install diagrams
#     cd docs/propuesta && python deploy.py
#     cp img/deploy.png ../wiki/img/deploy.png
from diagrams import Cluster, Diagram, Edge
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import User
from diagrams.onprem.container import Docker
from diagrams.onprem.vcs import Github
from diagrams.programming.language import Python, Typescript
from diagrams.saas.cdn import Cloudflare

ga = {"fontsize": "22", "pad": "0.5", "splines": "spline", "nodesep": "0.6", "ranksep": "0.9"}
na = {"fontsize": "13"}
ea = {"fontsize": "12"}

with Diagram(
    "trading_maxxing - Pipeline CI/CD y deployment (Cloudflare + motor en la Mac)",
    filename="img/deploy",
    show=False,
    direction="LR",
    graph_attr=ga,
    node_attr=na,
    edge_attr=ea,
):
    dev = User("Dev\nfeature/*")
    gh = Github("GitHub\nPR a develop")

    with Cluster("CI (cada PR) - ci.yml"):
        lint = GithubActions("Lint\nruff / eslint / tsc")
        py = Python("pytest --cov\n(>= 70 %)")
        ts = Typescript("vitest --coverage\napps/api y apps/web")
        build = GithubActions("docker build\n(matrix) y\nnext build (export)")

    with Cluster("Ambiente dev (merge a develop, automático)"):
        depApiDev = GithubActions("backend: d1 migrations\n+ wrangler deploy --env dev")
        depWebDev = GithubActions("frontend:\nwrangler pages deploy")
        uat = GithubActions("UAT (Playwright)\ncontra dev")

    with Cluster("Ambiente prod (merge a main + aprobación)"):
        approval = GithubActions("environment prod\nrequired reviewers")
        depApiProd = GithubActions("backend: d1 migrations\n+ wrangler deploy")
        depWebProd = GithubActions("frontend:\nwrangler pages deploy")

    hub = Docker("Docker Hub\nimágenes del motor\n(tag sha + canal)")

    with Cluster("Cloudflare dev"):
        apiDev = Cloudflare("Worker tm-api-dev\n+ D1 tm-dev")
        webDev = Cloudflare("Pages tm-web-dev\n(tras Access)")
    with Cluster("Cloudflare prod"):
        apiProd = Cloudflare("Worker tm-api\n+ D1 tm-prod")
        webProd = Cloudflare("Pages tm-web\n(tras Access)")

    mac = Docker("Mac de Carlo\ndocker compose\n+ Watchtower")

    dev >> Edge(label="push") >> gh >> lint
    lint >> py >> build
    lint >> ts >> build

    build >> Edge(label="merge a develop") >> depApiDev >> apiDev
    build >> Edge(label="merge a develop") >> depWebDev >> webDev
    build >> Edge(label="push imágenes") >> hub
    hub >> Edge(style="dotted", label="Watchtower pull") >> mac

    apiDev >> uat
    webDev >> uat
    uat >> Edge(label="PR develop -> main") >> approval
    approval >> depApiProd >> apiProd
    approval >> depWebProd >> webProd
