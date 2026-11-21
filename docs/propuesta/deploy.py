# trading_maxxing - diagrama de deployment (Azure + Cloudflare).
#
# Pendiente (#7): este script reemplaza al anterior, que dibujaba AWS ECS. Para
# regenerar la PNG:
#     pip install diagrams  (requiere Graphviz)
#     cd docs/propuesta && python deploy.py
#
# Iconos: https://learn.microsoft.com/azure/architecture/icons/
from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.container import Docker
from diagrams.onprem.client import User
from diagrams.onprem.network import Cloudflare
from diagrams.azure.compute import AppServices
from diagrams.programming.language import Python, Typescript
from diagrams.generic.blank import Blank

ga = {"fontsize": "20", "pad": "0.4", "nodesep": "0.6", "ranksep": "0.9"}
with Diagram(
    "trading_maxxing - Pipeline CI/CD y Deployment",
    filename="img/deploy",
    show=False,
    direction="LR",
    graph_attr=ga,
):
    dev = User("Dev\nfeature/*")
    gh = Github("GitHub\nPull Request")
    with Cluster("GitHub Actions - CI (cada PR)"):
        lint = GithubActions("Lint\nruff / eslint")
        py = Python("pytest + cov >= 70%")
        ts = Typescript("Vitest + RTL")
        build = GithubActions("docker build\n(matrix)")
    with Cluster("GitHub Actions - CD backend"):
        push = GithubActions("Push imagen\nsha / tag")
        depDev = GithubActions("Deploy a dev\naz webapps-deploy")
        depProd = GithubActions("Deploy a prod\n(required reviewers)")
    with Cluster("GitHub Actions - CD frontend"):
        pagesDev = GithubActions("Deploy Cloudflare Pages\ntm-web-dev")
        pagesProd = GithubActions("Deploy Cloudflare Pages\ntm-web (aprobacion)")
    hub = Docker("Docker Hub\ntrading-maxxing-*")
    with Cluster("Azure App Service Plan B1 - dev"):
        apiDev = AppServices("tm-api-dev")
        agentsDev = AppServices("tm-agents-dev")
    with Cluster("Azure App Service Plan B1 - prod"):
        apiProd = AppServices("tm-api-prod")
        agentsProd = AppServices("tm-agents-prod")
    cfDev = Cloudflare("tm-web-dev.pages.dev")
    cfProd = Cloudflare("tm-web.pages.dev")

    dev >> Edge(label="push") >> gh >> lint
    lint >> py
    lint >> ts
    py >> build
    ts >> build
    build >> Edge(label="merge a develop/main") >> push >> hub
    hub >> depDev
    depDev >> apiDev
    depDev >> agentsDev
    depDev >> Edge(label="tag + aprobacion") >> depProd
    depProd >> apiProd
    depProd >> agentsProd
    build >> Edge(label="develop") >> pagesDev >> cfDev
    pagesDev >> Edge(label="tag + aprobacion") >> pagesProd >> cfProd
