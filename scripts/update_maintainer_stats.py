#!/usr/bin/env python3

import json
import os
import re
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

USERNAME = "xLagerFeuer"
ORGANIZATION = "opensiro"
WINDOW_DAYS = 10
HISTORY_LOOKBACK_DAYS = 90
README_PATH = Path("README.md")
HISTORY_PATH = Path("stats/history.json")
START_MARKER = "<!-- MAINTAINER_STATS_START -->"
END_MARKER = "<!-- MAINTAINER_STATS_END -->"


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


def fetch_contributions(start_day: date, end_day: date) -> list[dict]:
    org_query = """
    query($org: String!) {
      organization(login: $org) { id }
    }
    """
    org_data = graphql(org_query, {"org": ORGANIZATION})
    org = org_data.get("organization")
    if not org:
        raise RuntimeError(f"Organization not found: {ORGANIZATION}")

    contributions_query = """
    query($user: String!, $orgId: ID!, $from: DateTime!, $to: DateTime!) {
      user(login: $user) {
        contributionsCollection(from: $from, to: $to, organizationID: $orgId) {
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
        contributions_query,
        {
            "user": USERNAME,
            "orgId": org["id"],
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


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        return {
            "username": USERNAME,
            "organization": ORGANIZATION,
            "window_days": WINDOW_DAYS,
            "days": {},
        }
    return json.loads(HISTORY_PATH.read_text())


def save_history(history: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    history["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history["days"] = dict(sorted(history["days"].items()))
    HISTORY_PATH.write_text(json.dumps(history, indent=2) + "\n")


def update_readme(avg: int, peak: int) -> None:
    text = README_PATH.read_text()
    replacement = (
        f"{START_MARKER}\n"
        f"**OpenSiro maintainer** · 10d avg **{avg} contributions/day** · peak **{peak}/day**\n"
        f"{END_MARKER}"
    )
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        flags=re.DOTALL,
    )
    if not pattern.search(text):
        raise RuntimeError("Maintainer stats markers are missing from README.md")
    README_PATH.write_text(pattern.sub(replacement, text))


def main() -> None:
    # Exclude today because it is incomplete and would depress the rolling average.
    last_complete_day = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_day = last_complete_day - timedelta(days=HISTORY_LOOKBACK_DAYS - 1)

    fetched = fetch_contributions(start_day, last_complete_day)
    history = load_history()
    for day in fetched:
        history["days"][day["date"]] = day["contributionCount"]

    rolling_start = last_complete_day - timedelta(days=WINDOW_DAYS - 1)
    rolling = [
        history["days"].get((rolling_start + timedelta(days=i)).isoformat(), 0)
        for i in range(WINDOW_DAYS)
    ]
    avg = int(sum(rolling) / WINDOW_DAYS + 0.5)
    peak = max(rolling)

    update_readme(avg, peak)
    save_history(history)
    print(f"10d avg: {avg}/day; peak: {peak}/day")


if __name__ == "__main__":
    main()
