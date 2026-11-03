#!/usr/bin/env python3
"""Generate the profile metric cards (light + dark SVG) and refresh the README.

Uses only the standard library. Reads the token from GH_TOKEN. With a personal
access token (repo + read:user scopes) private repositories and contributions
are included; with the default GITHUB_TOKEN only public data is visible.
"""

import json
import os
import re
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USER = os.environ.get("GH_USER", "Ramonizq")
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
README = ROOT / "README.md"

# Build/config languages say nothing about skills, so they are left out.
IGNORED_LANGUAGES = {"Makefile", "CMake", "Tcl", "Dockerfile", "Batchfile"}
LANGUAGE_ALIASES = {"Jupyter Notebook": "Python · Jupyter", "Python": "Python · Jupyter"}
TOP_LANGUAGES = 6

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"

THEMES = {
    "light": {
        "bg": "#ffffff",
        "border": "#d0d7de",
        "text": "#1f2328",
        "muted": "#656d76",
        "grid": "#eaeef2",
        "ramp": ["#0a3069", "#0550ae", "#0969da", "#218bff", "#54aeff", "#80ccff"],
        "other": "#d0d7de",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "text": "#e6edf3",
        "muted": "#8d96a0",
        "grid": "#21262d",
        "ramp": ["#cae8ff", "#a5d6ff", "#79c0ff", "#58a6ff", "#388bfd", "#1f6feb"],
        "other": "#30363d",
    },
}


# --------------------------------------------------------------------------- data

def graphql(query, variables=None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GH_TOKEN is not set")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "User-Agent": "profile-stats"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if "errors" in payload:
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]


PROFILE_QUERY = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, isFork: false) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""

YEAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch():
    repos, cursor = [], None
    while True:
        user = graphql(PROFILE_QUERY, {"login": USER, "cursor": cursor})["user"]
        page = user["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    created = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    days, commits = {}, 0
    # contributionsCollection spans at most one year, so walk the history in windows.
    start = created
    while start < now:
        end = min(start + timedelta(days=365), now)
        window = graphql(YEAR_QUERY, {"login": USER, "from": start.isoformat(), "to": end.isoformat()})
        collection = window["user"]["contributionsCollection"]
        commits += collection["totalCommitContributions"]
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]
        start = end

    languages = Counter()
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name not in IGNORED_LANGUAGES:
                languages[LANGUAGE_ALIASES.get(name, name)] += edge["size"]

    return {
        "created": created,
        "repos": user["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "commits": commits,
        "days": dict(sorted(days.items())),
        "languages": languages,
    }


def streaks(days):
    today = date.today().isoformat()
    longest = run = 0
    for day, count in days.items():
        if day > today:
            break
        run = run + 1 if count else 0
        longest = max(longest, run)
    # The current streak survives a day with no activity yet today.
    current = 0
    for day, count in reversed([(d, c) for d, c in days.items() if d <= today]):
        if count:
            current += 1
        elif day != today:
            break
    return current, longest


def summarize(data):
    today = date.today()
    year_ago = (today - timedelta(days=365)).isoformat()
    last_year = sum(c for d, c in data["days"].items() if d > year_ago)

    months = []
    for back in range(11, -1, -1):
        y, m = today.year, today.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        key = f"{y:04d}-{m:02d}"
        months.append((date(y, m, 1), sum(c for d, c in data["days"].items() if d.startswith(key))))

    weekdays = [0] * 7
    for day, count in data["days"].items():
        if day > year_ago:
            weekdays[date.fromisoformat(day).weekday()] += count

    current, longest = streaks(data["days"])
    active_days = sum(1 for d, c in data["days"].items() if d > year_ago and c)
    return {
        "last_year": last_year,
        "months": months,
        "weekdays": weekdays,
        "current": current,
        "longest": longest,
        "active_days": active_days,
        "years": (datetime.now(timezone.utc) - data["created"]).days // 365,
    }


# --------------------------------------------------------------------------- svg

def card(width, height, theme, inner):
    t = THEMES[theme]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}">'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" '
        f'fill="{t["bg"]}" stroke="{t["border"]}"/>{inner}</svg>'
    )


def heading(t, text, x=24, y=34, right=None, width=0):
    out = (f'<text x="{x}" y="{y}" fill="{t["muted"]}" font-size="11" font-weight="600" '
           f'letter-spacing="1.2">{text.upper()}</text>')
    if right:
        out += (f'<text x="{width - 24}" y="{y}" fill="{t["muted"]}" font-size="11" '
                f'text-anchor="end" font-family="{MONO}">{right}</text>')
    return out


def overview_svg(data, summary, theme):
    t = THEMES[theme]
    tiles = [
        (f"{summary['last_year']:,}", "contributions", "last 12 months"),
        (f"{sum(data['days'].values()):,}", "contributions", "all time"),
        (f"{data['repos']}", "repositories", f"{summary['years']} years on GitHub"),
        (f"{summary['active_days']}", "active days", "last 12 months"),
        (f"{len(data['languages'])}", "languages", "used across projects"),
    ]
    width, height = 840, 116
    step = width / len(tiles)
    inner = ""
    for i, (value, label, sub) in enumerate(tiles):
        x = step * i + 28
        if i:
            inner += f'<line x1="{step * i}" y1="26" x2="{step * i}" y2="90" stroke="{t["grid"]}"/>'
        inner += (
            f'<text x="{x}" y="58" fill="{t["text"]}" font-size="30" font-weight="600" '
            f'style="font-variant-numeric:tabular-nums">{value}</text>'
            f'<text x="{x}" y="79" fill="{t["text"]}" font-size="12.5">{label}</text>'
            f'<text x="{x}" y="96" fill="{t["muted"]}" font-size="11">{sub}</text>'
        )
    return card(width, height, theme, inner)


def languages_svg(data, theme):
    t = THEMES[theme]
    width, height = 412, 248
    total = sum(data["languages"].values()) or 1
    top = data["languages"].most_common(TOP_LANGUAGES)
    rest = total - sum(size for _, size in top)
    rows = [(name, size, t["ramp"][i]) for i, (name, size) in enumerate(top)]
    if rest > 0:
        rows.append(("Other", rest, t["other"]))

    inner = heading(t, "Languages", right="by code volume", width=width)
    x, bar_w = 24, width - 48
    inner += f'<clipPath id="bar"><rect x="24" y="52" width="{bar_w}" height="8" rx="4"/></clipPath><g clip-path="url(#bar)">'
    for _, size, color in rows:
        w = bar_w * size / total
        inner += f'<rect x="{x:.2f}" y="52" width="{w + 0.5:.2f}" height="8" fill="{color}"/>'
        x += w
    inner += "</g>"

    for i, (name, size, color) in enumerate(rows):
        col, row = i % 2, i // 2
        cx, cy = 24 + col * 186, 92 + row * 34
        inner += (
            f'<circle cx="{cx + 4}" cy="{cy - 4}" r="4" fill="{color}"/>'
            f'<text x="{cx + 16}" y="{cy}" fill="{t["text"]}" font-size="12.5">{name}</text>'
            f'<text x="{cx + 166}" y="{cy}" fill="{t["muted"]}" font-size="12" text-anchor="end" '
            f'font-family="{MONO}">{100 * size / total:.1f}%</text>'
        )
    return card(width, height, theme, inner)


def activity_svg(summary, theme):
    t = THEMES[theme]
    width, height = 412, 248
    inner = heading(t, "Contributions per month", right="last 12 months", width=width)
    months = summary["months"]
    peak = max((c for _, c in months), default=0) or 1
    left, base, top = 24, 206, 60
    slot = (width - 48) / len(months)
    bar = slot * 0.56
    for frac in (0.5, 1.0):
        y = base - (base - top) * frac
        inner += f'<line x1="{left}" y1="{y:.1f}" x2="{width - 24}" y2="{y:.1f}" stroke="{t["grid"]}" stroke-dasharray="2 3"/>'
    inner += f'<line x1="{left}" y1="{base}" x2="{width - 24}" y2="{base}" stroke="{t["border"]}"/>'
    for i, (month, count) in enumerate(months):
        h = (base - top) * count / peak
        x = left + slot * i + (slot - bar) / 2
        color = t["ramp"][2] if i == len(months) - 1 else t["ramp"][3]
        if h:
            inner += f'<rect x="{x:.1f}" y="{base - h:.1f}" width="{bar:.1f}" height="{h:.1f}" rx="2" fill="{color}"><title>{month:%b %Y}: {count}</title></rect>'
        inner += (f'<text x="{x + bar / 2:.1f}" y="{base + 18}" fill="{t["muted"]}" font-size="10" '
                  f'text-anchor="middle">{month:%b}</text>')
    inner += (f'<text x="{width - 24}" y="{top - 4}" fill="{t["muted"]}" font-size="10" text-anchor="end" '
              f'font-family="{MONO}">{peak}</text>')
    return card(width, height, theme, inner)


# --------------------------------------------------------------------------- readme

def refresh_readme(summary):
    if not README.exists():
        return
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    text = README.read_text()
    text = re.sub(
        r"(<!--UPDATED:START-->).*?(<!--UPDATED:END-->)",
        rf"\g<1>{stamp}\g<2>",
        text,
        flags=re.S,
    )
    README.write_text(text)


def main():
    data = fetch()
    summary = summarize(data)
    ASSETS.mkdir(exist_ok=True)
    for theme in THEMES:
        (ASSETS / f"overview-{theme}.svg").write_text(overview_svg(data, summary, theme))
        (ASSETS / f"languages-{theme}.svg").write_text(languages_svg(data, theme))
        (ASSETS / f"activity-{theme}.svg").write_text(activity_svg(summary, theme))
    refresh_readme(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "months"}, default=str))


if __name__ == "__main__":
    main()
