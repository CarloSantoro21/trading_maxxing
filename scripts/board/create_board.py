#!/usr/bin/env python3
"""Crea labels, milestones, issues y el GitHub Project (Board) de trading_maxxing.

Requisitos: GitHub CLI autenticado con permiso de proyectos:
    gh auth login
    gh auth refresh -s project
Uso:
    python3 create_board.py                 # repo por defecto
    python3 create_board.py OWNER/REPO
"""
import json, subprocess, sys, pathlib

REPO = sys.argv[1] if len(sys.argv) > 1 else "CarloSantoro21/trading_maxxing"
OWNER = REPO.split("/")[0]
PROJECT_TITLE = "trading_maxxing"
data = json.loads((pathlib.Path(__file__).parent / "issues.json").read_text(encoding="utf-8"))


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        print("ERROR:", " ".join(args[:3]), r.stderr.strip())
    return r


print("Labels...")
for name, color, desc in data["labels"]:
    gh("label", "create", name, "--repo", REPO, "--color", color, "--description", desc, "--force")

print("Milestones...")
existing = {m["title"] for m in json.loads(gh("api", f"repos/{REPO}/milestones?state=all&per_page=100").stdout or "[]")}
for title, due in data["milestones"]:
    if title not in existing:
        gh("api", "-X", "POST", f"repos/{REPO}/milestones", "-f", f"title={title}", "-f", f"due_on={due}T23:59:00Z")

print("Project...")
proj = json.loads(gh("project", "create", "--owner", OWNER, "--title", PROJECT_TITLE, "--format", "json").stdout)
number = proj["number"]
gh("project", "link", str(number), "--owner", OWNER, "--repo", REPO, check=False)

print("Issues...")
open_titles = {i["title"] for i in json.loads(gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "200", "--json", "title").stdout or "[]")}
for it in data["issues"]:
    if it["title"] in open_titles:
        continue
    args = ["issue", "create", "--repo", REPO, "--title", it["title"], "--body", it["body"], "--milestone", it["milestone"]]
    for lb in it["labels"]:
        args += ["--label", lb]
    if gh(*args).stdout.strip():
        print("  +", it["title"])

print("Agregando issues al Project...")
for iss in json.loads(gh("issue", "list", "--repo", REPO, "--state", "all", "--limit", "200", "--json", "url").stdout or "[]"):
    gh("project", "item-add", str(number), "--owner", OWNER, "--url", iss["url"])

print(f"\nListo. Abre https://github.com/users/{OWNER}/projects/{number} y cambia a vista Board.")
print("Sugerencia: en la vista Board agrega las columnas Backlog, To Do, In Progress, In Review, Done (campo Status).")
