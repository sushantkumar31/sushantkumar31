#!/usr/bin/env python3
"""Auto-generate all dynamic profile assets and inject them into README.md.

Runs inside GitHub Actions (daily cron + on config changes). User only ever
edits profile/config.json — everything else updates itself.

Generated pieces:
  - assets/stats.svg           (baked GitHub stats card)
  - assets/top-langs.svg       (baked top-languages card)
  - assets/streak.svg          (baked streak card)
  - assets/activity-graph.svg  (baked contribution graph)
  - assets/typing.svg          (baked typing animation)
  - README About               (bullets from config)
  - README Skills              (badges from config)
  - README Projects            (cards from config)
  - README Connect             (socials from config)
  - README Current Focus       (progress bars from config)
  - README Recent Activity     (last public GitHub events)
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONFIG = ROOT / "profile" / "config.json"
README = ROOT / "README.md"

USER = "sushantkumar31"
FOCUS_MARKER = ("<!-- FOCUS:START -->", "<!-- FOCUS:END -->")
ACTIVITY_MARKER = ("<!-- ACTIVITY:START -->", "<!-- ACTIVITY:END -->")
ABOUT_MARKER = ("<!-- ABOUT:START -->", "<!-- ABOUT:END -->")
SKILLS_MARKER = ("<!-- SKILLS:START -->", "<!-- SKILLS:END -->")
PROJECTS_MARKER = ("<!-- PROJECTS:START -->", "<!-- PROJECTS:END -->")
SOCIALS_MARKER = ("<!-- SOCIALS:START -->", "<!-- SOCIALS:END -->")

BAR_LENGTH = 20  # segments; one segment == 5%
MAX_FETCH_ATTEMPTS = 3
RETRY_DELAY_S = 3

# Some upstream render services gate the request count; a browser-like UA helps.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 profile-bot"
)


def log(msg: str) -> None:
    print(f"[profile-bot] {msg}", flush=True)


def _is_url(value: str) -> bool:
    """Loose check for http(s) or mailto links."""
    value = (value or "").strip()
    return value.startswith("http://") or value.startswith("https://") or value.startswith("mailto:")


def validate_config(config: dict) -> None:
    """Sanity checks with clear, actionable messages; exit early on a broken
    config instead of silently shipping broken content."""
    problems = []
    for key, kind in (("about", list), ("skills", list), ("projects", list), ("socials", list), ("learning", list)):
        if key in config and not isinstance(config[key], kind):
            problems.append(f"`{key}` must be a list, got {type(config[key]).__name__}")

    for item in config.get("learning", []):
        if not isinstance(item, dict) or "topic" not in item:
            problems.append(f"learning item `{item!r}` must be an object with a `topic`")
            continue
        pct = item.get("progress")
        if pct is None or not isinstance(pct, (int, float)) or not (0 <= int(pct) <= 100):
            problems.append(f"learning item `{item['topic']}` needs `progress` as a number in 0-100")

    seen_repos = set()
    for i, p in enumerate(config.get("projects", [])):
        if not isinstance(p, dict):
            problems.append(f"project #{i + 1} must be an object")
            continue
        repo = p.get("repo")
        if not repo:
            problems.append(f"project `{p.get('name', '?')}` is missing `repo`")
        elif repo in seen_repos:
            problems.append(f"project repo `{repo}` is duplicated in the config")
        else:
            seen_repos.add(repo)
        if p.get("demo") and not _is_url(p["demo"]):
            problems.append(f"project `{p.get('name', repo)}` demo `{p['demo']}` is not a valid URL")

    for i, s in enumerate(config.get("socials", [])):
        if not s.get("url") or not _is_url(s["url"]):
            problems.append(f"socials #{i + 1} (`{s.get('name', '?')}`) needs a valid `url`")

    for i, sk in enumerate(config.get("skills", [])):
        if not isinstance(sk, dict) or not sk.get("name") or not sk.get("logo"):
            problems.append(f"skills #{i + 1} must have both `name` and `logo`")

    if problems:
        for p in problems:
            log(f"ERROR config: {p}")
        sys.exit(1)


def is_valid_svg(data: bytes) -> bool:
    """Reject upstream HTML error pages or empty bodies masquerading as SVG.

    Tolerates an optional leading HTML comment (some services prepend one)."""
    text = data[:2048].decode("utf-8", errors="ignore").lstrip()
    while text.startswith("<!--"):
        close = text.find("-->")
        if close == -1:
            return False
        text = text[close + 3:].lstrip()
    if not text.startswith("<svg"):
        return False
    for bad in ("Failed to retrieve", "Error lable", "Internal Server Error", "Rate limit"):
        if bad in text:
            return False
    return True


def fetch(url: str, dest: str) -> bool:
    """Download a remote SVG into assets/. Retries with backoff, never
    overwrites a good local asset on failure."""
    last_err = None
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except Exception as exc:  # noqa: BLE001 - keep old asset on outage
            last_err = exc
            log(f"WARN  attempt {attempt}/{MAX_FETCH_ATTEMPTS} failed to fetch {dest}: {exc}")
            time.sleep(RETRY_DELAY_S * attempt)
            continue
        if not is_valid_svg(data):
            last_err = ValueError("response is not a valid SVG")
            log(f"WARN  attempt {attempt}/{MAX_FETCH_ATTEMPTS}: {dest} is not valid SVG")
            time.sleep(RETRY_DELAY_S * attempt)
            continue
        ASSETS.mkdir(exist_ok=True)
        (ASSETS / dest).write_bytes(data)
        log(f"OK    {dest} ({len(data)} bytes)")
        return True
    log(f"WARN  giving up on {dest}; keeping previous asset ({last_err})")
    return False


def fetch_assets(config: dict) -> None:
    """Bake all remote cards into local assets so the profile never breaks."""
    fetch(
        "https://github-readme-stats.shion.dev/api"
        f"?username={USER}&show_icons=true&theme=tokyonight&hide_border=true"
        "&include_all_commits=true&count_private=true",
        "stats.svg",
    )
    fetch(
        "https://github-readme-stats.shion.dev/api/top-langs/"
        f"?username={USER}&layout=compact&theme=tokyonight&hide_border=true",
        "top-langs.svg",
    )
    fetch(
        f"https://streak-stats.demolab.com?user={USER}"
        "&theme=tokyonight&hide_border=true&short_numbers=true",
        "streak.svg",
    )
    fetch(
        "https://github-readme-activity-graph.vercel.app/graph"
        f"?username={USER}&theme=tokyo-night&hide_border=true&area=true"
        "&area_color=7AA2F7&bg_color=1a1b26&color=7aa2f7"
        "&line=7aa2f7&point=c0caf5",
        "activity-graph.svg",
    )
    lines = ";".join(config.get("typing_lines", ["Building in public"]))
    typing_url = (
        "https://readme-typing-svg.demolab.com?"
        + urllib.parse.urlencode(
            {
                "font": "Fira Code",
                "weight": "500",
                "size": "17",
                "duration": "3000",
                "pause": "900",
                "color": "7AA2F7",
                "center": "true",
                "vCenter": "true",
                "repeat": "true",
                "width": "640",
                "height": "45",
                "lines": lines,
            }
        )
    )
    fetch(typing_url, "typing.svg")


def focus_block(config: dict) -> str:
    """Render ASCII progress bars from config values (clamped 0-100)."""
    bars = []
    for item in config.get("learning", config.get("focus", [])):
        pct = max(0, min(100, int(item.get("progress", 0))))
        filled = round(pct / 100 * BAR_LENGTH)
        bar = "▓" * filled + "░" * (BAR_LENGTH - filled)
        bars.append(f"[{bar}] {item['topic']} — {pct}%")
    return "\n".join(bars) or "Nothing configured yet."


def about_block(config: dict) -> str:
    """Render the about bullets from config."""
    items = config.get("about", [])
    if not items:
        return "_Add an `about` list to `profile/config.json`._"
    return "\n".join(f"- {it.get('emoji', '•')} {it.get('text', '')}" for it in items)


def skills_block(config: dict) -> str:
    """Render skill badges from config, wrapped in a centered div."""
    skills = config.get("skills", [])
    if not skills:
        return "_Add a `skills` list to `profile/config.json`._"
    badges = []
    for s in skills:
        name = s.get("name", "")
        logo = s.get("logo", "").lower()
        color = s.get("color", "24283b")
        b = (
            f"![{name}](https://img.shields.io/badge/"
            f"{urllib.parse.quote(name)}-24283b?style=flat-square"
            f"&logo={logo_slug}&logoColor=7aa2f7&labelColor=24283b&color={color})"
        )
        badges.append(b)
    return '<div align="center">\n\n' + "\n".join(badges) + "\n\n</div>"


def projects_block(config: dict) -> str:
    """Render featured projects as a two-column card table from config."""
    projects = config.get("projects", [])
    if not projects:
        return "_Add a `projects` list to `profile/config.json`._"

    def card(p: dict) -> str:
        repo = p.get("repo", "")
        parts = []
        parts.append(f"**{p.get('emoji', '📁')} {p.get('name', '')}**")
        parts.append(f"{p.get('description', '')}")
        badges = (
            f"[![Code](https://img.shields.io/badge/Source_Code-24283b?"
            f"style=flat-square&logo=github&logoColor=7aa2f7)]"
            f"(https://github.com/{USER}/{repo})"
        )
        if p.get("demo"):
            badges += (
                f" [![Live](https://img.shields.io/badge/Live_Demo-9ece6a?"
                f"style=flat-square&logo=streamlit&logoColor=1a1b26)]"
                f"({p['demo']})"
            )
        parts.append(badges)
        return "<br/>".join(parts)

    if len(projects) == 1:
        return f"<div align=\"center\">\n\n| |\n|:---:|\n| {card(projects[0])} |\n\n</div>"
    rows = []
    for i in range(0, len(projects), 2):
        left = card(projects[i])
        if i + 1 < len(projects):
            right = card(projects[i + 1])
            rows.append(f"| {left} | {right} |")
        else:
            # Odd count: let the last card span the full width.
            rows.append(f"| {left} |")
    table = "\n".join(rows)
    header = "| | |\n|:---:|:---:|" if len(rows) > 1 else "| |\n|:---:|"
    return f"<div align=\"center\">\n\n{header}\n{table}\n\n</div>"


def socials_block(config: dict) -> str:
    """Render connect badges from config."""
    socials = config.get("socials", [])
    if not socials:
        return "_Add a `socials` list to `profile/config.json`._"
    badges = []
    for s in socials:
        logo = s.get("logo", "").lower()
        b = (
            f"[![{s.get('name', '')}](https://img.shields.io/badge/"
            f"{urllib.parse.quote(s.get('name', ''))}-24283b?"
            f"style=for-the-badge&logo={logo}&logoColor=7aa2f7)]"
            f"({s.get('url', '#')})"
        )
        badges.append(b)
    return '<div align="center">\n\n' + "\n".join(badges) + "\n\n</div>"


def esc(text: str) -> str:
    """Escape characters that would break inline markdown links/backticks."""
    return text.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("_", "\\_")


def recent_activity(token: str) -> str:
    """Pull the latest public events and render a human-readable list."""
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(
                f"https://api.github.com/users/{USER}/events?per_page=12",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                events = json.load(resp)
            break
        except Exception as exc:  # noqa: BLE001
            log(f"WARN  attempt {attempt}/{MAX_FETCH_ATTEMPTS} activity fetch failed: {exc}")
            if attempt == MAX_FETCH_ATTEMPTS:
                return "Latest activity will appear here after the next refresh."
            time.sleep(RETRY_DELAY_S * attempt)

    pushes: list[dict] = []  # consecutive same-repo/same-day pushes get grouped
    lines: list[str] = []
    for ev in events:
        kind = ev.get("type")
        full_repo = ev.get("repo", {}).get("name", "")
        repo = full_repo.split("/")[-1]
        day = ev.get("created_at", "")[:10]
        payload = ev.get("payload", {}) or {}
        if full_repo == f"{USER}/{USER}":
            continue  # skip the profile repo's own auto-refresh pushes
        if kind == "PushEvent":
            n = payload.get("size")
            if n is None:
                n = len(payload.get("commits", [])) or 1
            if pushes and pushes[-1]["repo"] == repo and pushes[-1]["day"] == day:
                pushes[-1]["count"] += n
            else:
                pushes.append({"repo": repo, "day": day, "count": n})
        elif kind == "PullRequestEvent":
            title = (payload.get("pull_request") or {}).get("title", "")
            action = payload.get("action", "updated").title()
            suffix = f": _{esc(title)}_" if title else ""
            lines.append(f"- 🔀 {action} PR in `{esc(repo)}`{suffix} — {day}")
        elif kind == "IssuesEvent":
            title = (payload.get("issue") or {}).get("title", "")
            action = payload.get("action", "updated").title()
            suffix = f": _{esc(title)}_" if title else ""
            lines.append(f"- 🐛 {action} issue in `{esc(repo)}`{suffix} — {day}")
        elif kind == "IssueCommentEvent":
            lines.append(f"- 💬 Commented on an issue in `{esc(repo)}` — {day}")
        elif kind == "PullRequestReviewEvent":
            lines.append(f"- 👀 Reviewed a PR in `{esc(repo)}` — {day}")
        elif kind == "WatchEvent":
            lines.append(f"- ⭐ Starred `{esc(repo)}` — {day}")
        elif kind == "ReleaseEvent":
            lines.append(f"- 🚀 Released {esc((payload.get('release') or {}).get('tag_name', 'a version'))} in `{esc(repo)}` — {day}")
        elif kind == "CreateEvent":
            lines.append(f"- 🎉 Created a {esc(payload.get('ref_type', 'ref'))} in `{esc(repo)}` — {day}")
        elif kind == "ForkEvent":
            lines.append(f"- 🍴 Forked `{esc(repo)}` — {day}")

    for p in pushes:
        noun = "commit" if p["count"] == 1 else "commits"
        lines.append(f"- 🔨 Pushed **{p['count']}** {noun} to `{esc(p['repo'])}` — {p['day']}")
    return "\n".join(lines) or "No recent public activity yet."


def replace_between_markers(content: str, marker: tuple, block: str) -> str:
    start, end = marker
    if start not in content or end not in content:
        log(f"WARN  marker pair {start} missing in README; skipping")
        return content
    head, _, rest = content.partition(start)
    _, _, tail = rest.partition(end)
    return head + start + "\n" + block + "\n" + end + tail


def main() -> None:
    if not CONFIG.exists():
        log("ERROR profile/config.json missing")
        sys.exit(1)

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    token = os.environ.get("GITHUB_TOKEN", "")

    validate_config(config)

    log("fetching remote assets")
    fetch_assets(config)

    log("rewriting README sections")
    readme = README.read_text(encoding="utf-8")
    readme = replace_between_markers(readme, ABOUT_MARKER, about_block(config))
    readme = replace_between_markers(readme, SKILLS_MARKER, skills_block(config))
    readme = replace_between_markers(readme, PROJECTS_MARKER, projects_block(config))
    readme = replace_between_markers(readme, SOCIALS_MARKER, socials_block(config))
    readme = replace_between_markers(readme, FOCUS_MARKER, focus_block(config))
    readme = replace_between_markers(readme, ACTIVITY_MARKER, recent_activity(token))
    README.write_text(readme, encoding="utf-8")

    log("done")


if __name__ == "__main__":
    main()