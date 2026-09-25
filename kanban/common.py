"""The configuration and the GitHub access the kanban scripts share."""
import datetime as dt
import json
import os
import subprocess
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# The board's columns in order. The scripts match items by these names.
STATUSES = ("Backlog", "Ready", "In progress", "In review", "Done")


@dataclass(frozen=True)
class Size:
    name: str
    nominal: float
    maximum: int
    color: str


@dataclass(frozen=True)
class Meeting:
    id: str
    name: str
    weekday: str
    time: dt.time
    minutes: int
    first_week: int
    every: int
    description: str


@dataclass(frozen=True)
class Config:
    owner: str
    number: int
    repository: str
    timezone: ZoneInfo
    start: dt.date
    weeks: int
    team_size: int
    product_owner: str
    person_items: int
    person_days: float
    review_items: int
    sizes: tuple
    meetings: tuple
    calendar_name: str

    def size(self, name):
        return next((s for s in self.sizes if s.name == name), None)

    def week_start(self, week):
        """The Monday of week `week`, counting week 1 from the period start."""
        return self.start + dt.timedelta(weeks=week - 1)


def load_config(path):
    return parse_config(Path(path).read_text(encoding="utf-8"))


def parse_config(text):
    raw = tomllib.loads(text)
    project, period, team, limits = raw["project"], raw["period"], raw["team"], raw["limits"]
    start = period["start"]
    if not isinstance(start, dt.date) or start.weekday() != 0:
        raise ValueError(f"period.start must be a Monday, got {start!r}")
    sizes = tuple(
        Size(s["name"], s["nominal"], s["maximum"], s["color"]) for s in raw["sizes"]
    )
    if not sizes:
        raise ValueError("at least one size is needed")
    meetings = tuple(
        Meeting(
            m["id"], m["name"], m["weekday"], m["time"], m["minutes"],
            m.get("first_week", 1), m.get("every", 1), m.get("description", ""),
        )
        for m in raw.get("meetings", [])
    )
    return Config(
        owner=project["owner"],
        number=project["number"],
        repository=project["repository"],
        timezone=ZoneInfo(project["timezone"]),
        start=start,
        weeks=period["weeks"],
        team_size=team["size"],
        product_owner=team["product_owner"],
        person_items=limits["person_items"],
        person_days=limits["person_days"],
        review_items=limits["review_items"],
        sizes=sizes,
        meetings=meetings,
        calendar_name=raw.get("calendar", {}).get("name", f"{project['repository'].split('/')[-1]} meetings"),
    )


def token():
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    return subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()


def request(tok, method, url, body=None):
    """One call to the GitHub API. Returns the decoded JSON body."""
    req = urllib.request.Request(
        url if url.startswith("https://") else f"https://api.github.com{url}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"bearer {tok}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as response:
        data = response.read()
    return json.loads(data) if data else None


def graphql(tok, query, variables):
    body = request(tok, "POST", "/graphql", {"query": query, "variables": variables})
    if body.get("errors"):
        raise RuntimeError(json.dumps(body["errors"]))
    return body["data"]
