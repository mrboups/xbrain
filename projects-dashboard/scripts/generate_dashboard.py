#!/usr/bin/env python3
"""
generate_dashboard.py — Génère public/index.html depuis GitHub API + memory-api.

Variables d'environnement requises :
  GITHUB_ORG            — Org GitHub (défaut: your-github-org)
  GITHUB_USER           — Compte GitHub perso (défaut: mrboups)
  GITHUB_API_PAT        — Fine-grained PAT read:org read:repo
  XBRAIN_MEMORY_API_URL — URL de l'API xbrain (défaut: https://api.grooveos.app)
  XBRAIN_BRIDGE_JWT     — JWT de service pour memory-api

Comportement :
  - Interroge GitHub API pour l'org ET le compte perso.
  - Interroge memory-api /v1/admin/projects pour les métadonnées.
  - Génère public/index.html (HTML+CSS vanilla, auto-suffisant, offline-ready).
  - En cas d'erreur partielle (rate limit, réseau), génère la page avec les données
    disponibles + note "données partielles". Exit 0 toujours.
"""

from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Tenter d'importer requests ; fallback urllib pour les environnements sans pip
try:
    import requests

    def _get(url: str, headers: dict) -> tuple[int, Any]:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            return r.status_code, r.json() if r.content else {}
        except Exception as exc:  # noqa: BLE001
            print(f"[generate_dashboard] WARN GET {url}: {exc}", file=sys.stderr)
            return 0, {}

except ImportError:
    import urllib.request
    import urllib.error

    def _get(url: str, headers: dict) -> tuple[int, Any]:  # type: ignore[misc]
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                return resp.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except Exception as exc:  # noqa: BLE001
            print(f"[generate_dashboard] WARN GET {url}: {exc}", file=sys.stderr)
            return 0, {}


# ---------------------------------------------------------------------------
# Configuration depuis l'environnement
# ---------------------------------------------------------------------------

GITHUB_ORG = os.environ.get("GITHUB_ORG", "dejavudev")
GITHUB_USER = os.environ.get("GITHUB_USER", "mrboups")
GITHUB_API_PAT = os.environ.get("GITHUB_API_PAT", "")
XBRAIN_MEMORY_API_URL = os.environ.get("XBRAIN_MEMORY_API_URL", "https://api.grooveos.app")
XBRAIN_BRIDGE_JWT = os.environ.get("XBRAIN_BRIDGE_JWT", "")

OUTPUT_DIR = Path(__file__).parent.parent / "public"
OUTPUT_FILE = OUTPUT_DIR / "index.html"


def gh_headers() -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_API_PAT:
        h["Authorization"] = f"Bearer {GITHUB_API_PAT}"
    return h


# ---------------------------------------------------------------------------
# Récupération des données GitHub
# ---------------------------------------------------------------------------

def fetch_github_repos() -> tuple[list[dict], bool]:
    """Retourne (repos org, ok)."""
    url = f"https://api.github.com/orgs/{GITHUB_ORG}/repos?per_page=100&sort=updated"
    status, data = _get(url, gh_headers())
    if status == 200 and isinstance(data, list):
        return data, True
    print(f"[generate_dashboard] WARN GitHub org repos API: HTTP {status}", file=sys.stderr)
    return [], False


def fetch_github_user_repos() -> tuple[list[dict], bool]:
    """Retourne (repos perso du GITHUB_USER, ok) — exclut les forks."""
    url = f"https://api.github.com/users/{GITHUB_USER}/repos?per_page=100&sort=updated&type=owner"
    status, data = _get(url, gh_headers())
    if status == 200 and isinstance(data, list):
        return [r for r in data if not r.get("fork")], True
    print(f"[generate_dashboard] WARN GitHub user repos API: HTTP {status}", file=sys.stderr)
    return [], False


def fetch_github_collaborators(owner: str, repo_name: str) -> list[str]:
    """Retourne la liste de logins des collaborateurs d'un repo."""
    url = f"https://api.github.com/repos/{owner}/{repo_name}/collaborators?per_page=100"
    status, data = _get(url, gh_headers())
    if status == 200 and isinstance(data, list):
        return [m.get("login", "") for m in data if m.get("login")]
    return []


# ---------------------------------------------------------------------------
# Récupération des projets xbrain
# ---------------------------------------------------------------------------

XBRAIN_TEAM_SCOPE = os.environ.get("XBRAIN_TEAM_SCOPE", "")


def fetch_xbrain_projects() -> tuple[list[dict], bool]:
    """Retourne (projets, ok)."""
    qs = f"?team_scope={XBRAIN_TEAM_SCOPE}" if XBRAIN_TEAM_SCOPE else ""
    url = f"{XBRAIN_MEMORY_API_URL}/v1/admin/projects{qs}"
    headers = {"Content-Type": "application/json"}
    if XBRAIN_BRIDGE_JWT:
        headers["Authorization"] = f"Bearer {XBRAIN_BRIDGE_JWT}"
    status, data = _get(url, headers)
    if status == 200:
        if isinstance(data, list):
            return data, True
        if isinstance(data, dict):
            projects = data.get("projects", data.get("items", []))
            if isinstance(projects, list):
                return projects, True
    print(f"[generate_dashboard] WARN xbrain projects API: HTTP {status}", file=sys.stderr)
    return [], False


# ---------------------------------------------------------------------------
# Jointure et enrichissement
# ---------------------------------------------------------------------------

def _resolve_deploy_target(raw: str) -> str:
    raw = raw.lower()
    if "firebase" in raw:
        return "Firebase"
    if "cloudrun" in raw or "cloud_run" in raw or "cloud-run" in raw:
        return "Cloud Run"
    return raw.upper() or "VM"


def build_project_cards(
    github_repos: list[dict],
    github_user_repos: list[dict],
    xbrain_projects: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Retourne (cards_org, cards_user)."""
    # Index xbrain par slug et par nom
    xbrain_by_slug: dict[str, dict] = {}
    xbrain_by_name: dict[str, dict] = {}
    for proj in xbrain_projects:
        slug = proj.get("slug") or proj.get("project_scope") or ""
        if slug:
            xbrain_by_slug[slug] = proj
        name = (proj.get("name") or "").lower().replace(" ", "-")
        if name:
            xbrain_by_name[name] = proj

    def _build_card(repo: dict, owner: str) -> dict:
        repo_name = repo.get("name", "")
        repo_slug = repo_name.lower().replace("_", "-")
        xbrain_info = xbrain_by_slug.get(repo_slug) or xbrain_by_name.get(repo_slug)
        members = fetch_github_collaborators(owner, repo_name) if GITHUB_API_PAT else []
        deploy_target = "VM"
        project_url = ""
        project_scope = ""
        team_scope = ""
        is_indexed = False
        if xbrain_info:
            deploy_target = _resolve_deploy_target(xbrain_info.get("deploy_target", ""))
            project_url = xbrain_info.get("url", "")
            project_scope = xbrain_info.get("project_scope", "")
            team_scope = xbrain_info.get("team_scope", "")
            is_indexed = True
        return {
            "name": repo_name,
            "slug": repo_slug,
            "description": repo.get("description") or "",
            "html_url": repo.get("html_url", f"https://github.com/{owner}/{repo_name}"),
            "project_url": project_url,
            "deploy_target": deploy_target,
            "is_indexed": is_indexed,
            "members": members[:10],
            "project_scope": project_scope,
            "team_scope": team_scope,
            "updated_at": repo.get("updated_at", ""),
        }

    # Cards org
    seen_slugs: set[str] = set()
    org_cards: list[dict] = []
    for repo in github_repos:
        card = _build_card(repo, GITHUB_ORG)
        org_cards.append(card)
        if card["is_indexed"]:
            seen_slugs.add(card["slug"])

    # Projets xbrain sans repo GitHub org correspondant → ajoutés à org_cards
    for proj in xbrain_projects:
        slug = proj.get("slug") or proj.get("project_scope") or ""
        if slug and slug not in seen_slugs:
            org_cards.append({
                "name": proj.get("name", slug),
                "slug": slug,
                "description": "",
                "html_url": f"https://github.com/{GITHUB_ORG}/{slug}",
                "project_url": proj.get("url", ""),
                "deploy_target": _resolve_deploy_target(proj.get("deploy_target", "")),
                "is_indexed": True,
                "members": [],
                "project_scope": proj.get("project_scope", ""),
                "team_scope": proj.get("team_scope", ""),
                "updated_at": "",
            })

    # Cards compte perso (exclure les repos déjà présents dans l'org)
    org_names = {r.get("name", "").lower() for r in github_repos}
    user_cards: list[dict] = []
    for repo in github_user_repos:
        if repo.get("name", "").lower() not in org_names:
            user_cards.append(_build_card(repo, GITHUB_USER))

    return org_cards, user_cards


# ---------------------------------------------------------------------------
# Génération HTML
# ---------------------------------------------------------------------------

BADGE_COLORS = {
    "Firebase": "#1a73e8",
    "Cloud Run": "#34a853",
    "VM": "#757575",
}


def _badge(label: str) -> str:
    color = BADGE_COLORS.get(label, "#757575")
    safe_label = html.escape(label)
    return (
        f'<span class="badge" style="background:{color}">'
        f"{safe_label}</span>"
    )


def _status_badge(is_indexed: bool) -> str:
    if is_indexed:
        return '<span class="status status-live">live</span>'
    return '<span class="status status-unindexed">non-indexé</span>'


def _member_avatars(members: list[str]) -> str:
    if not members:
        return '<span class="no-members">aucun membre</span>'
    parts = []
    for login in members:
        safe_login = html.escape(login)
        parts.append(
            f'<img class="avatar" '
            f'src="https://github.com/{safe_login}.png?size=24" '
            f'alt="{safe_login}" title="{safe_login}" '
            f'onerror="this.style.display=\'none\'">'
        )
    return "".join(parts)


def _card_html(card: dict) -> str:
    name = html.escape(card["name"])
    description = html.escape(card["description"]) if card["description"] else ""
    slug = html.escape(card["slug"])
    project_scope = html.escape(card["project_scope"]) if card["project_scope"] else ""
    team_scope = html.escape(card["team_scope"]) if card["team_scope"] else ""
    members_html = _member_avatars(card["members"])

    # Titre : lien vers l'URL déployée si disponible, sinon vers GitHub
    if card["project_url"]:
        safe_url = html.escape(card["project_url"])
        title_html = f'<a href="{safe_url}" target="_blank" rel="noopener">{name}</a>'
    else:
        safe_gh_url = html.escape(card["html_url"])
        title_html = f'<a href="{safe_gh_url}" target="_blank" rel="noopener">{name}</a>'

    meta_parts = []
    if project_scope:
        meta_parts.append(f"project: {project_scope}")
    if team_scope:
        meta_parts.append(f"team: {team_scope}")
    meta_html = " &middot; ".join(meta_parts) if meta_parts else ""

    return f"""
    <div class="card"
         data-name="{slug}"
         data-members="{html.escape(','.join(card['members']))}">
      <div class="card-header">
        <h3 class="card-title">{title_html}</h3>
        <div class="card-badges">
          {_badge(card['deploy_target'])}
          {_status_badge(card['is_indexed'])}
        </div>
      </div>
      {f'<p class="card-description">{description}</p>' if description else ''}
      <div class="card-members">{members_html}</div>
      {f'<p class="card-meta">{meta_html}</p>' if meta_html else ''}
    </div>"""


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: #f8f9fa;
  color: #212529;
  min-height: 100vh;
}
header {
  background: #1a1a2e;
  color: #fff;
  padding: 1.5rem 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
}
header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.02em; }
header .subtitle { font-size: 0.85rem; opacity: 0.7; }
.filters {
  padding: 1.5rem 2rem 1rem;
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}
.filters input {
  padding: 0.5rem 0.75rem;
  border: 1px solid #dee2e6;
  border-radius: 6px;
  font-size: 0.9rem;
  width: 220px;
  background: #fff;
}
.filters input:focus {
  outline: none;
  border-color: #1a73e8;
  box-shadow: 0 0 0 3px rgba(26,115,232,0.15);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 1.25rem;
  padding: 1rem 2rem 2rem;
}
.card {
  background: #fff;
  border: 1px solid #e9ecef;
  border-radius: 10px;
  padding: 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  transition: box-shadow 0.15s;
}
.card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.5rem;
}
.card-title {
  font-size: 1rem;
  font-weight: 600;
}
.card-title a {
  color: #1a1a2e;
  text-decoration: none;
}
.card-title a:hover { text-decoration: underline; }
.card-badges { display: flex; gap: 0.4rem; flex-shrink: 0; align-items: center; }
.badge {
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  white-space: nowrap;
}
.status {
  font-size: 0.72rem;
  font-weight: 600;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
}
.status-live { background: #d4edda; color: #155724; }
.status-unindexed { background: #e9ecef; color: #495057; }
.card-description {
  font-size: 0.85rem;
  color: #6c757d;
  line-height: 1.4;
}
.card-members { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
.avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px solid #dee2e6;
}
.no-members { font-size: 0.8rem; color: #adb5bd; font-style: italic; }
.card-meta { font-size: 0.78rem; color: #868e96; }
.data-warning {
  margin: 0.75rem 2rem;
  padding: 0.75rem 1rem;
  background: #fff3cd;
  border: 1px solid #ffc107;
  border-radius: 6px;
  font-size: 0.85rem;
  color: #856404;
}
.empty-state {
  grid-column: 1/-1;
  text-align: center;
  padding: 3rem;
  color: #868e96;
  font-size: 1rem;
}
footer {
  text-align: center;
  padding: 1.5rem;
  font-size: 0.8rem;
  color: #868e96;
  border-top: 1px solid #e9ecef;
  background: #fff;
  margin-top: 1rem;
}
section { margin-bottom: 2rem; }
.section-header {
  display: flex;
  align-items: baseline;
  gap: 1rem;
  padding: 1rem 2rem 0.25rem;
  border-bottom: 2px solid #e9ecef;
  margin: 0 2rem 0;
}
.section-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: #1a1a2e;
}
.section-meta {
  font-size: 0.82rem;
  color: #868e96;
}
"""

JS = """
function filterCards() {
  const memberFilter = document.getElementById('filter-member').value.toLowerCase().trim();
  const projectFilter = document.getElementById('filter-project').value.toLowerCase().trim();
  const cards = document.querySelectorAll('.card');
  let visible = 0;
  cards.forEach(function(card) {
    const name = (card.dataset.name || '').toLowerCase();
    const members = (card.dataset.members || '').toLowerCase();
    const matchMember = !memberFilter || members.includes(memberFilter);
    const matchProject = !projectFilter || name.includes(projectFilter);
    if (matchMember && matchProject) {
      card.style.display = '';
      visible++;
    } else {
      card.style.display = 'none';
    }
  });
  const empty = document.getElementById('empty-state');
  if (empty) {
    empty.style.display = visible === 0 ? '' : 'none';
  }
}
document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('filter-member').addEventListener('input', filterCards);
  document.getElementById('filter-project').addEventListener('input', filterCards);
});
"""


def _section_html(title: str, subtitle: str, cards: list[dict], section_id: str) -> str:
    cards_html = "\n".join(_card_html(c) for c in cards)
    if not cards_html:
        cards_html = '<div class="empty-state">Aucun projet trouvé.</div>'
    else:
        cards_html += f'\n    <div class="empty-state" id="empty-{section_id}" style="display:none">Aucun résultat pour ces filtres.</div>'
    indexed = sum(1 for c in cards if c["is_indexed"])
    return f"""
  <section>
    <div class="section-header">
      <h2 class="section-title">{html.escape(title)}</h2>
      <span class="section-meta">{html.escape(subtitle)} &middot; {indexed} indexés</span>
    </div>
    <div class="grid" data-section="{section_id}">
      {cards_html}
    </div>
  </section>"""


def generate_html(
    org_cards: list[dict],
    user_cards: list[dict],
    generated_at: str,
    partial: bool,
) -> str:
    warning_html = ""
    if partial:
        warning_html = (
            '<div class="data-warning">'
            "Données partielles — certaines sources API étaient inaccessibles lors de la génération."
            "</div>"
        )

    total = len(org_cards) + len(user_cards)
    org_section = _section_html(
        f"Org · {GITHUB_ORG}",
        f"{len(org_cards)} projets",
        org_cards,
        "org",
    )
    user_section = _section_html(
        f"Compte · {GITHUB_USER}",
        f"{len(user_cards)} repos",
        user_cards,
        "user",
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>xbrain Projects</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>xbrain Projects</h1>
      <div class="subtitle">{total} projets au total</div>
    </div>
    <div class="subtitle">Généré le {html.escape(generated_at)}</div>
  </header>
  {warning_html}
  <div class="filters">
    <input id="filter-member" type="text" placeholder="Filtrer par membre..." aria-label="Filtrer par membre">
    <input id="filter-project" type="text" placeholder="Filtrer par projet..." aria-label="Filtrer par projet">
  </div>
  {org_section}
  {user_section}
  <footer>Généré le {html.escape(generated_at)} — xbrain v5</footer>
  <script>
{JS}
  </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    partial = False

    print(f"[generate_dashboard] Fetching GitHub repos for org: {GITHUB_ORG}")
    github_repos, github_ok = fetch_github_repos()
    if not github_ok:
        partial = True

    print(f"[generate_dashboard] Fetching GitHub repos for user: {GITHUB_USER}")
    github_user_repos, user_ok = fetch_github_user_repos()
    if not user_ok:
        partial = True

    print(f"[generate_dashboard] Fetching xbrain projects from {XBRAIN_MEMORY_API_URL}")
    xbrain_projects, xbrain_ok = fetch_xbrain_projects()
    if not xbrain_ok:
        partial = True

    print(f"[generate_dashboard] Building cards ({len(github_repos)} org repos, {len(github_user_repos)} user repos, {len(xbrain_projects)} xbrain projects)")
    org_cards, user_cards = build_project_cards(github_repos, github_user_repos, xbrain_projects)

    html_content = generate_html(org_cards, user_cards, generated_at, partial)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html_content, encoding="utf-8")

    print(f"[generate_dashboard] Generated {OUTPUT_FILE} ({len(org_cards)} org cards, {len(user_cards)} user cards, partial={partial})")


if __name__ == "__main__":
    main()
