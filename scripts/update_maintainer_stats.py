#!/usr/bin/env python3

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

USERNAME = "xLagerFeuer"
WINDOW_DAYS = 10
HISTORY_LOOKBACK_DAYS = 90
HISTORY_PATH = Path("stats/history.json")
CARD_PATH = Path("assets/maintainer-activity.svg")


def graphql(query: str, variables: dict) -> dict:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")

    payload = json.dumps({"query": query, "variables": variables}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "opensiro-maintainer-stats",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        body = json.load(response)

    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"], indent=2))
    return body["data"]


def public_search_count(query: str) -> int:
    # Deliberately unauthenticated: the repository-scoped Actions token can
    # undercount cross-repository public search results outside this repo.
    params = urllib.parse.urlencode({"q": query, "per_page": 1})
    request = urllib.request.Request(
        f"https://api.github.com/search/issues?{params}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "opensiro-maintainer-stats",
        },
    )
    with urllib.request.urlopen(request) as response:
        body = json.load(response)
    return int(body["total_count"])


def fetch_contributions(start_day: date, end_day: date) -> list[dict]:
    query = """
    query($user: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $user) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """

    from_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(end_day, time.max, tzinfo=timezone.utc)
    data = graphql(
        query,
        {
            "user": USERNAME,
            "from": from_dt.isoformat().replace("+00:00", "Z"),
            "to": to_dt.isoformat().replace("+00:00", "Z"),
        },
    )

    user = data.get("user")
    if not user:
        raise RuntimeError(f"User not found: {USERNAME}")

    weeks = user["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [day for week in weeks for day in week["contributionDays"]]
    return [day for day in days if start_day.isoformat() <= day["date"] <= end_day.isoformat()]


def fetch_window_breakdown(start_day: date, end_day: date) -> dict:
    query = """
    query($user: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $user) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          commitContributionsByRepository(maxRepositories: 100) {
            repository { nameWithOwner }
          }
          pullRequestContributionsByRepository(maxRepositories: 100) {
            repository { nameWithOwner }
          }
          issueContributionsByRepository(maxRepositories: 100) {
            repository { nameWithOwner }
          }
        }
      }
    }
    """

    from_dt = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    to_dt = datetime.combine(end_day, time.max, tzinfo=timezone.utc)
    data = graphql(
        query,
        {
            "user": USERNAME,
            "from": from_dt.isoformat().replace("+00:00", "Z"),
            "to": to_dt.isoformat().replace("+00:00", "Z"),
        },
    )

    user = data.get("user")
    if not user:
        raise RuntimeError(f"User not found: {USERNAME}")

    collection = user["contributionsCollection"]
    repositories = set()
    for key in (
        "commitContributionsByRepository",
        "pullRequestContributionsByRepository",
        "issueContributionsByRepository",
    ):
        for contribution in collection.get(key, []):
            repository = contribution.get("repository") or {}
            name = repository.get("nameWithOwner")
            if name:
                repositories.add(name)

    date_range = f"{start_day.isoformat()}..{end_day.isoformat()}"
    merged_prs = public_search_count(
        f"author:{USERNAME} is:pr is:merged merged:{date_range}"
    )
    issues = public_search_count(
        f"author:{USERNAME} is:issue created:{date_range}"
    )

    return {
        "merged_prs": merged_prs,
        "issues": issues,
        "commits": collection["totalCommitContributions"],
        "repos": len(repositories),
    }


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        return {"username": USERNAME, "scope": "github", "window_days": WINDOW_DAYS, "days": {}}
    return json.loads(HISTORY_PATH.read_text())


def save_history(history: dict, as_of: date, avg: int, peak: int, breakdown: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history["username"] = USERNAME
    history["scope"] = "github"
    history.pop("organization", None)
    history["window_days"] = WINDOW_DAYS
    history["summary"] = {
        "as_of": as_of.isoformat(),
        "avg_10d": avg,
        "peak_10d": peak,
        "merged_prs_10d": breakdown["merged_prs"],
        "issues_10d": breakdown["issues"],
        "commits_10d": breakdown["commits"],
        "repos_10d": breakdown["repos"],
    }
    history["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history["days"] = dict(sorted(history["days"].items()))
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n")


def render_card(days: list[date], values: list[int], avg: int, peak: int, breakdown: dict) -> None:
    width, height = 900, 190
    gx0, gx1 = 340, 620
    gy0, gy1 = 92, 154
    low, high = min(values), max(values)
    spread = max(high - low, 1)

    points = []
    for i, value in enumerate(values):
        x = gx0 + (gx1 - gx0) * i / (len(values) - 1)
        y = gy1 - (value - low) / spread * (gy1 - gy0)
        points.append((x, y))

    points_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    motion_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    avg_y = gy1 - (avg - low) / spread * (gy1 - gy0)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.8"/>' for x, y in points)
    first_label = days[0].strftime("%b %d")
    last_label = days[-1].strftime("%b %d")

    merged_prs = breakdown["merged_prs"]
    issues = breakdown["issues"]
    commits = breakdown["commits"]
    repos = breakdown["repos"]

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">OpenSiro maintainer activity</title>
  <desc id="desc">{avg} GitHub contributions per day over the last 10 completed days, with a peak of {peak}. The same window includes {merged_prs} merged pull requests, {issues} opened issues, {commits} commit contributions, and activity across {repos} repositories.</desc>
  <rect x="0.5" y="0.5" width="899" height="189" rx="14" fill="#ffffff" stroke="#d0d7de"/>
  <g fill="#18181b" font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,Liberation Mono,monospace">
    <text x="30" y="34" font-size="12" font-weight="700" letter-spacing="1.4">OPENSIRO / MAINTAINER ACTIVITY</text>
    <text x="870" y="34" text-anchor="end" font-size="10" fill="#6e7781" letter-spacing="0.8">PROFILE-WIDE GITHUB</text>

    <text x="30" y="94" font-size="42" font-weight="700">{avg}</text>
    <text x="30" y="118" font-size="12" fill="#57606a">contributions/day · 10d avg</text>
    <text x="210" y="94" font-size="30" font-weight="700">{peak}</text>
    <text x="210" y="118" font-size="12" fill="#57606a">peak / day</text>

    <line x1="{gx0}" y1="{avg_y:.1f}" x2="{gx1}" y2="{avg_y:.1f}" stroke="#d8dee4" stroke-width="1" stroke-dasharray="4 5"/>
    <polyline points="{points_attr}" fill="none" stroke="#18181b" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
    {dots}
    <text x="{gx0}" y="177" font-size="10" fill="#6e7781">{first_label}</text>
    <text x="{gx1}" y="177" text-anchor="end" font-size="10" fill="#6e7781">{last_label}</text>

    <g>
      <text x="660" y="70" font-size="9" font-weight="700" fill="#6e7781" letter-spacing="1.1">10D ACTIVITY</text>

      <text x="660" y="100" font-size="21" font-weight="700">{merged_prs}</text>
      <text x="660" y="115" font-size="10" fill="#57606a">PRs · merged</text>

      <text x="770" y="100" font-size="21" font-weight="700">{issues}</text>
      <text x="770" y="115" font-size="10" fill="#57606a">issues</text>

      <text x="660" y="146" font-size="21" font-weight="700">{commits}</text>
      <text x="660" y="161" font-size="10" fill="#57606a">commits</text>

      <text x="770" y="146" font-size="21" font-weight="700">{repos}</text>
      <text x="770" y="161" font-size="10" fill="#57606a">repos</text>
    </g>
  </g>

  <g aria-hidden="true">
    <g>
      <text x="0" y="-8" text-anchor="middle" dominant-baseline="middle" font-family="'Noto Sans JP','Hiragino Sans','Yu Gothic',sans-serif" font-size="18" font-weight="600" fill="#18181b">白</text>
      <animateMotion dur="5s" repeatCount="indefinite" rotate="0" path="{motion_path}"/>
    </g>
  </g>
</svg>
'''

    CARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    CARD_PATH.write_text(svg)


def main() -> None:
    # Exclude today because it is incomplete and would depress the rolling average.
    last_complete_day = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_day = last_complete_day - timedelta(days=HISTORY_LOOKBACK_DAYS - 1)

    fetched = fetch_contributions(start_day, last_complete_day)
    history = load_history()
    for day in fetched:
        history["days"][day["date"]] = day["contributionCount"]

    rolling_start = last_complete_day - timedelta(days=WINDOW_DAYS - 1)
    rolling_days = [rolling_start + timedelta(days=i) for i in range(WINDOW_DAYS)]
    rolling = [history["days"].get(day.isoformat(), 0) for day in rolling_days]
    avg = int(sum(rolling) / WINDOW_DAYS + 0.5)
    peak = max(rolling)
    breakdown = fetch_window_breakdown(rolling_start, last_complete_day)

    render_card(rolling_days, rolling, avg, peak, breakdown)
    save_history(history, last_complete_day, avg, peak, breakdown)
    print(
        f"10d avg: {avg}/day; peak: {peak}/day; "
        f"merged PRs: {breakdown['merged_prs']}; issues: {breakdown['issues']}; "
        f"commits: {breakdown['commits']}; repos: {breakdown['repos']}; card: {CARD_PATH}"
    )


if __name__ == "__main__":
    main()
