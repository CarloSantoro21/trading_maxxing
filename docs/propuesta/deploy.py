from diagrams import Diagram, Cluster, Edge
from diagrams.onprem.vcs import Github
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.container import Docker
from diagrams.onprem.client import User
from diagrams.aws.compute import ECS
from diagrams.programming.language import Python, Typescript
from diagrams.generic.blank import Blank
ga = {"fontsize":"20","pad":"0.4","nodesep":"0.6","ranksep":"0.9"}
with Diagram("trading_maxxing - Pipeline CI/CD y Deployment", filename="img/deploy", show=False, direction="LR", graph_attr=ga):
    dev = User("Dev\nfeature/*")
    gh = Github("GitHub\nPull Request")
    with Cluster("GitHub Actions - CI (cada PR)"):
        lint = GithubActions("Lint\nruff / eslint")
        py = Python("Pruebas unitarias\npytest + coverage >= 70%")
        ts = Typescript("Pruebas unitarias\nVitest + RTL")
        build = GithubActions("docker build\n(matrix 5 servicios)")
    with Cluster("GitHub Actions - CD"):
        push = GithubActions("Login + push\nsha / version")
        dep = GithubActions("Deploy\naws ecs update-service")
    hub = Docker("Docker Hub\nusuario/trading-maxxing-*")
    with Cluster("AWS ECS Fargate"):
        stg = ECS("staging\n(rama develop)")
        prod = ECS("produccion\n(tag vX.Y.Z en main)")
    dev >> Edge(label="push") >> gh >> lint
    lint >> py
    lint >> ts
    py >> build
    ts >> build
    build >> Edge(label="merge a develop/main") >> push >> hub
    hub >> dep
    dep >> Edge(label="develop") >> stg
    dep >> Edge(label="tag + aprobacion") >> prod
