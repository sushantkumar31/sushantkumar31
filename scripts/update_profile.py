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
  - README Current Focus       (progress bars from config)
  - README Recent Activity     (last public GitHub events)
"""

import json
import os
import sys
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

BAR_LENGTH = 20  # segments; one segment == 5%


def log(msg: str) -> None:
    print(f"[profile-bot] {msg}", flush=True)


def fetch(url: str, dest: str) -> bool:
    """Download a remote SVG into assets/. Never overwrites on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "profile-bot"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001 - keep old asset on outage
        log(f"WARN  failed to fetch {dest}: {exc}")
        return False
    text = data.decode("utf-8", errors="ignore")
    if "Failed to retrieve" in text or "Error lable" in text:
        log(f"WARN  {dest} contains an upstream error body; keeping previous asset")
        return False
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / dest).write_bytes(data)
    log(f"OK    {dest} ({len(data)} bytes)")
    return True


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
    lines = ";" .join(config.get("typing_lines", ["Building in public"]))
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
    for item in config.get("focus", []):
        pct = max(0, min(100, int(item.get("progress", 0))))
        filled = round(pct / 100 * BAR_LENGTH)
        bar = "▓" * filled + "░" * (BAR_LENGTH - filled)
        bars.append(f"[{bar}] {item['topic']} — {pct}%")
    return "\n".join(bars) or "Nothing configured yet."


def recent_activity(token: str) -> str:
    """Pull the latest public events and render a human-readable list."""
    headers = {"User-Agent": "profile-bot"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"https://api.github.com/users/{USER}/events?per_page=12",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            events = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        log(f"WARN  activity fetch failed: {exc}")
        return "Latest activity will appear here after the next refresh."
    lines = []
    for ev in events:
        kind = ev.get("type")
        repo = ev.get("repo", {}).get("name", "").split("/")[-1]
        full_repo = ev.get("repo", {}).get("name", "")
        day = ev.get("created_at", "")[:10]
        payload = ev.get("payload", {})
        if full_repo == f"{USER}/{USER}":
            continue  # skip the profile repo's own auto-refresh pushes
        if kind == "PushEvent":
            n = payload.get("size")
            if n is None:
                n = len(payload.get("commits", [])) or 1
            lines.append(f"- 🔨 Pushed **{n}** commit(s) to `{repo}` — {day}")
        elif kind == "PullRequestEvent":
            lines.append(f"- 🔀 {payload.get('action', 'updated').title()} a PR in `{repo}` — {day}")
        elif kind == "IssuesEvent":
            lines.append(f"- 🐛 {payload.get('action', 'updated').title()} an issue in `{repo}` — {day}")
        elif kind == "CreateEvent":
            lines.append(f"- 🎉 Created a {payload.get('ref_type', 'ref')} in `{repo}` — {day}")
        elif kind == "ForkEvent":
            lines.append(f"- 🍴 Forked `{repo}` — {day}")
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

    log("fetching remote assets")
    fetch_assets(config)

    log("rewriting README sections")
    readme = README.read_text(encoding="utf-8")
    readme = replace_between_markers(readme, FOCUS_MARKER, focus_block(config))
    readme = replace_between_markers(readme, ACTIVITY_MARKER, recent_activity(token))
    README.write_text(readme, encoding="utf-8")

    log("done")


if __name__ == "__main__":
    main()