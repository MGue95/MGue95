#!/usr/bin/env python3
"""Render the profile's GitHub analytics cards as static SVGs.

Runs in GitHub Actions, writes assets/stats.svg, assets/languages.svg and
assets/activity.svg. No third-party service is involved: the data comes from
the GitHub GraphQL API, the SVG is written here, the result is committed.

The token decides what is visible. A personal access token with `repo` and
`read:org` includes contributions to private repositories (GitHub reports
those as an aggregate, never per repository); the workflow's default
GITHUB_TOKEN only sees public activity.
"""

import datetime as dt
import json
import os
import sys
import urllib.request
from collections import OrderedDict

API = "https://api.github.com/graphql"
LOGIN = os.environ.get("PROFILE_LOGIN", "MGue95")
# Comma-separated language names to keep out of the language card, e.g. "HTML,CSS".
EXCLUDED = {
    name.strip().lower()
    for name in os.environ.get("EXCLUDE_LANGUAGES", "").split(",")
    if name.strip()
}
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

# Palette — matches assets/header.svg
BG = "#0D1117"
ACCENT = "#22C55E"
ACCENT_SOFT = "#4ADE80"
TEXT = "#C9D1D9"
MUTED = "#8B949E"
GRID = "#1E3A2B"
FONT = "'Segoe UI', -apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif"


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": "bearer %s" % TOKEN,
            "Content-Type": "application/json",
            "User-Agent": "MGue95-profile-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise RuntimeError(json.dumps(payload["errors"]))
    return payload["data"]


def esc(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def group(number):
    return "{:,}".format(int(number)).replace(",", ".")


# --- data ------------------------------------------------------------------

COUNTS_Q = """
query($login:String!, $merged:String!) {
  user(login:$login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(
      first:1
      includeUserRepositories:true
      contributionTypes:[COMMIT, PULL_REQUEST, REPOSITORY, PULL_REQUEST_REVIEW, ISSUE]
    ) { totalCount }
  }
  mergedPullRequests: search(query:$merged, type:ISSUE, first:1) { issueCount }
}
"""

LANGUAGES_Q = """
query($login:String!, $cursor:String) {
  user(login:$login) {
    repositoriesContributedTo(
      first:100
      after:$cursor
      includeUserRepositories:true
      contributionTypes:[COMMIT, PULL_REQUEST, REPOSITORY]
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        languages(first:12, orderBy:{field:SIZE, direction:DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      restrictedContributionsCount
    }
  }
}
"""

CALENDAR_Q = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch():
    counts = gql(
        COUNTS_Q,
        {"login": LOGIN, "merged": "author:%s is:pr is:merged" % LOGIN},
    )
    user = counts["user"]

    langs = {}
    cursor = None
    while True:
        block = gql(LANGUAGES_Q, {"login": LOGIN, "cursor": cursor})["user"][
            "repositoriesContributedTo"
        ]
        for repo in block["nodes"]:
            for edge in repo["languages"]["edges"]:
                if edge["node"]["name"].lower() in EXCLUDED:
                    continue
                entry = langs.setdefault(
                    edge["node"]["name"], {"size": 0, "color": edge["node"]["color"]}
                )
                entry["size"] += edge["size"]
                if edge["node"]["color"]:
                    entry["color"] = edge["node"]["color"]
        if not block["pageInfo"]["hasNextPage"]:
            break
        cursor = block["pageInfo"]["endCursor"]

    # GitHub only exposes private contributions as a yearly aggregate, so the
    # all-time total has to be summed year by year.
    all_time = 0
    for year in range(int(user["createdAt"][:4]), dt.datetime.now(dt.timezone.utc).year + 1):
        data = gql(
            YEAR_Q,
            {
                "login": LOGIN,
                "from": "%d-01-01T00:00:00Z" % year,
                "to": "%d-12-31T23:59:59Z" % year,
            },
        )["user"]["contributionsCollection"]
        all_time += data["totalCommitContributions"] + data["restrictedContributionsCount"]

    today = dt.datetime.now(dt.timezone.utc).date()
    cal = gql(
        CALENDAR_Q,
        {
            "login": LOGIN,
            "from": (today - dt.timedelta(days=364)).isoformat() + "T00:00:00Z",
            "to": today.isoformat() + "T23:59:59Z",
        },
    )["user"]["contributionsCollection"]["contributionCalendar"]
    days = [
        (day["date"], day["contributionCount"])
        for week in cal["weeks"]
        for day in week["contributionDays"]
    ]

    return {
        "all_time": all_time,
        "last_year": cal["totalContributions"],
        "prs": user["pullRequests"]["totalCount"],
        "prs_merged": counts["mergedPullRequests"]["issueCount"],
        "issues": user["issues"]["totalCount"],
        "repos": user["repositoriesContributedTo"]["totalCount"],
        "followers": user["followers"]["totalCount"],
        "languages": langs,
        "days": days,
        "streak": longest_streak(days),
    }


def longest_streak(days):
    best = current = 0
    for _date, count in days:
        current = current + 1 if count else 0
        best = max(best, current)
    return best


# --- cards -----------------------------------------------------------------


def card_open(width, height, label):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 %d %d" role="img" aria-label="%s">\n'
        '  <rect width="%d" height="%d" rx="8" fill="%s"/>\n'
        % (width, height, width, height, esc(label), width, height, BG)
    )


def heading(text, subtitle=None, x=25):
    out = (
        '  <text x="%d" y="34" font-family="%s" font-size="16" font-weight="600" '
        'fill="%s">%s</text>\n' % (x, FONT, ACCENT, esc(text))
    )
    if subtitle:
        out += (
            '  <text x="%d" y="52" font-family="%s" font-size="11" fill="%s">%s</text>\n'
            % (x, FONT, MUTED, esc(subtitle))
        )
    return out


def render_stats(data):
    rows = [
        ("Contributions, last 12 months", data["last_year"]),
        ("Pull requests opened", data["prs"]),
        ("Pull requests merged", data["prs_merged"]),
        ("Repositories contributed to", data["repos"]),
    ]
    # Only worth a row of its own once there is history beyond the last year.
    if data["all_time"] > data["last_year"]:
        rows.insert(0, ("Contributions, all time", data["all_time"]))
    else:
        rows.append(("Longest daily streak", data["streak"]))
    w, h = 495, 195
    svg = [
        card_open(w, h, "GitHub statistics for %s" % LOGIN),
        heading("GitHub Stats", "public and private activity combined"),
        '  <rect x="25" y="64" width="445" height="1" fill="%s"/>\n' % GRID,
    ]
    y = 90
    for label, value in rows:
        svg.append(
            '  <text x="25" y="%d" font-family="%s" font-size="13" fill="%s">%s</text>\n'
            % (y, FONT, TEXT, esc(label))
        )
        svg.append(
            '  <text x="470" y="%d" text-anchor="end" font-family="%s" font-size="13" '
            'font-weight="600" fill="%s">%s</text>\n' % (y, FONT, ACCENT_SOFT, group(value))
        )
        y += 23
    svg.append("</svg>\n")
    return "".join(svg)


def render_languages(data, count=8):
    ranked = sorted(data["languages"].items(), key=lambda kv: kv[1]["size"], reverse=True)
    total = sum(entry["size"] for _, entry in ranked) or 1
    # Anything below 0.1 % renders as "0.0 %" and only adds noise.
    top = OrderedDict(
        [(n, e) for n, e in ranked[:count] if 100.0 * e["size"] / total >= 0.1]
    )
    shown = sum(entry["size"] for entry in top.values()) or 1
    w, h = 495, 195
    svg = [
        card_open(w, h, "Most used languages of %s" % LOGIN),
        heading("Top Languages", "by bytes across the repositories I work in"),
    ]

    bar_x, bar_w, bar_y, bar_h = 25, 445, 66, 9
    svg.append(
        '  <clipPath id="barclip"><rect x="%d" y="%d" width="%d" height="%d" rx="%d"/></clipPath>\n'
        % (bar_x, bar_y, bar_w, bar_h, bar_h // 2)
    )
    svg.append('  <g clip-path="url(#barclip)">\n')
    offset = float(bar_x)
    for _name, entry in top.items():
        seg = bar_w * (entry["size"] / float(shown))
        svg.append(
            '    <rect x="%.2f" y="%d" width="%.2f" height="%d" fill="%s"/>\n'
            % (offset, bar_y, seg + 0.6, bar_h, entry["color"] or MUTED)
        )
        offset += seg
    svg.append("  </g>\n")

    col_x = [25, 260]
    for index, (name, entry) in enumerate(top.items()):
        x = col_x[index % 2]
        y = 106 + (index // 2) * 23
        pct = 100.0 * entry["size"] / total
        svg.append(
            '  <circle cx="%d" cy="%d" r="5" fill="%s"/>\n'
            % (x + 5, y - 4, entry["color"] or MUTED)
        )
        svg.append(
            '  <text x="%d" y="%d" font-family="%s" font-size="12" fill="%s">%s</text>\n'
            % (x + 18, y, FONT, TEXT, esc(name))
        )
        svg.append(
            '  <text x="%d" y="%d" text-anchor="end" font-family="%s" font-size="12" '
            'font-weight="600" fill="%s">%.1f&#8201;%%</text>\n' % (x + 200, y, FONT, MUTED, pct)
        )
    svg.append("</svg>\n")
    return "".join(svg)


def render_activity(data):
    days = data["days"]
    w, h = 1000, 300
    left, right, top_pad, bottom = 56, 24, 78, 54
    plot_w = w - left - right
    plot_h = h - top_pad - bottom
    peak = max([count for _, count in days] + [1])
    step = plot_w / float(max(len(days) - 1, 1))

    def point(index, count):
        return (left + index * step, top_pad + plot_h - (count / float(peak)) * plot_h)

    svg = [
        card_open(w, h, "Contribution activity of %s over the last year" % LOGIN),
        '  <defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%%" stop-color="%s" stop-opacity="0.45"/>'
        '<stop offset="100%%" stop-color="%s" stop-opacity="0.02"/>'
        "</linearGradient></defs>\n" % (ACCENT, ACCENT_SOFT),
        heading(
            "Contribution Activity",
            "%s contributions in the last 12 months · peak %s in a day · "
            "longest streak %s days"
            % (group(data["last_year"]), group(peak), group(data["streak"])),
        ),
    ]

    for fraction in (0, 0.5, 1):
        y = top_pad + plot_h - fraction * plot_h
        svg.append(
            '  <line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1"/>\n'
            % (left, y, w - right, y, GRID)
        )
        svg.append(
            '  <text x="%d" y="%.1f" text-anchor="end" font-family="%s" font-size="11" '
            'fill="%s">%d</text>\n' % (left - 10, y + 4, FONT, MUTED, round(peak * fraction))
        )

    coords = [point(i, c) for i, (_, c) in enumerate(days)]
    line = " ".join("%.2f,%.2f" % xy for xy in coords)
    svg.append(
        '  <polygon points="%.2f,%.2f %s %.2f,%.2f" fill="url(#area)"/>\n'
        % (left, top_pad + plot_h, line, left + (len(days) - 1) * step, top_pad + plot_h)
    )
    svg.append(
        '  <polyline points="%s" fill="none" stroke="%s" stroke-width="1.6" '
        'stroke-linejoin="round"/>\n' % (line, ACCENT)
    )

    last_month = None
    for index, (date, _count) in enumerate(days):
        if date[:7] == last_month:
            continue
        last_month = date[:7]
        x, _y = point(index, 0)
        svg.append(
            '  <text x="%.1f" y="%d" text-anchor="middle" font-family="%s" font-size="11" '
            'fill="%s">%s</text>\n'
            % (x, h - 22, FONT, MUTED, esc(dt.date.fromisoformat(date).strftime("%b %y")))
        )
    svg.append("</svg>\n")
    return "".join(svg)


def main():
    if not TOKEN:
        sys.exit("No token: set GH_TOKEN or GITHUB_TOKEN.")
    data = fetch()
    cards = {
        "stats.svg": render_stats(data),
        "languages.svg": render_languages(data),
        "activity.svg": render_activity(data),
    }
    os.makedirs(OUT, exist_ok=True)
    for name, svg in cards.items():
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as handle:
            handle.write(svg)
        print("wrote assets/%s (%d bytes)" % (name, len(svg)))


if __name__ == "__main__":
    main()
