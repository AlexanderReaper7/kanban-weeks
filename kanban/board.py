#!/usr/bin/env python3
"""Checks a Kanban board against the rules in docs/kanban.md.

Reports, in this order:

1. Syncs needed: a work item past its size's maximum. Days count from the day
   the item entered In progress to today, both included, weekends excluded.
   Review is separate: an item In review is not checked against a maximum.
2. Work-in-progress limits: per person the most items and nominal days In
   progress, and the most items In review for the whole team.
3. Replenishment: Ready holds fewer items than there are people.
4. Rule breaks: a supertask (an issue with sub-issues) with a Size, a work item
   past Backlog without one, an item in Ready with someone assigned, an item In
   progress with nobody assigned, an item In progress whose start is unknown.

The numbers come from kanban.toml. The script cannot check that an item in
Ready has acceptance criteria or no open question to the product owner, or
that a Size stayed the same after the item left Ready.

The start of an item is the later of two dates: when its Status last moved
into In progress from Backlog or Ready, if GitHub recorded that, and its Start
date field. GitHub records no event when the API moves an item, so the Start
date is what the check relies on. --write sets Start date to today on every
item In progress without one. Run with --write every working day, or the day
it records is later than the real start. An item sent back from In review to
In progress keeps its Start date, so its days in review then count.

--comment posts the report on the repository's closed issue labelled
"board report", which setup_board.py creates, when it differs from the last
report posted. Findings then exit 0, because the comment is the report.

The token comes from GH_TOKEN or GITHUB_TOKEN, else from `gh auth token`. It
needs the project scope, and repo for a private repository.

Run: python3 kanban/board.py [--config kanban.toml] [--write] [--comment]
     (exit 0 nothing to report, 1 with findings, 2 on an API error)
"""
import argparse
import datetime as dt
import subprocess
import sys
import urllib.error
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import graphql, load_config, request, token  # noqa: E402

REPORT_LABEL = "board report"

QUERY = """
query($owner: String!, $number: Int!, $after: String) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        field(name: "Start date") { ... on ProjectV2Field { id } }
        items(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
            size: fieldValueByName(name: "Size") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
            start: fieldValueByName(name: "Start date") { ... on ProjectV2ItemFieldDateValue { date } }
            content {
              __typename
              ... on DraftIssue { title assignees(first: 10) { nodes { login } } }
              ... on Issue {
                number title
                assignees(first: 10) { nodes { login } }
                subIssuesSummary { total }
                timelineItems(last: 50, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) {
                  nodes { ... on ProjectV2ItemStatusChangedEvent { createdAt previousStatus status project { id } } }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

SET_DATE = """
mutation($project: ID!, $item: ID!, $field: ID!, $date: Date!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {date: $date}}) {
    clientMutationId
  }
}
"""

COMMENTS = """
query($owner: String!, $name: String!, $number: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    issue(number: $number) { comments(last: 50) { nodes { author { login } body } } }
  }
}
"""


def fetch(tok, cfg):
    """Returns the project id, the Start date field id, and every item as a dict."""
    items, after = [], None
    while True:
        owner = graphql(tok, QUERY, {"owner": cfg.owner, "number": cfg.number, "after": after})["repositoryOwner"]
        project = owner and owner.get("projectV2")
        if not project:
            raise RuntimeError(f"no project {cfg.owner}/{cfg.number}")
        if not project["field"]:
            raise RuntimeError("the project has no Start date field; run setup_board.py")
        for node in project["items"]["nodes"]:
            items.append(item_from(node, project["id"], cfg.timezone))
        page = project["items"]["pageInfo"]
        if not page["hasNextPage"]:
            return project["id"], project["field"]["id"], items
        after = page["endCursor"]


def item_from(node, project_id, tz):
    content = node["content"] or {}
    events = [
        e for e in (content.get("timelineItems") or {}).get("nodes", [])
        if e and e["project"]["id"] == project_id
    ]
    entered = [
        dt.datetime.fromisoformat(e["createdAt"]).astimezone(tz).date()
        for e in events
        if e["status"] == "In progress" and e["previousStatus"] in (None, "Backlog", "Ready")
    ]
    start = node["start"] and dt.date.fromisoformat(node["start"]["date"])
    starts = [d for d in (max(entered, default=None), start) if d]
    return {
        "id": node["id"],
        "ref": f"#{content['number']}" if "number" in content else "draft",
        "title": content.get("title", "(no title)"),
        "status": node["status"] and node["status"]["name"],
        "size": node["size"] and node["size"]["name"],
        "start": max(starts, default=None),
        "has_start_date": bool(start),
        "assignees": [a["login"] for a in (content.get("assignees") or {}).get("nodes", [])],
        "supertask": (content.get("subIssuesSummary") or {}).get("total", 0) > 0,
    }


def working_days(start, today):
    """Weekdays from start to today, both included."""
    days = 0
    day = start
    while day <= today:
        days += day.weekday() < 5
        day += dt.timedelta(days=1)
    return days


def check(items, today, cfg):
    """Returns findings as (heading, line) pairs, in report order."""
    findings = []

    for i in items:
        size = cfg.size(i["size"])
        if i["status"] != "In progress" or i["supertask"] or not size or not i["start"]:
            continue
        days = working_days(i["start"], today)
        if days > size.maximum:
            findings.append(("Sync needed", f"{label(i)}: {size.name}, {days} working days in progress, maximum {size.maximum}"))

    per_person = defaultdict(list)
    for i in items:
        if i["status"] == "In progress" and not i["supertask"]:
            for person in i["assignees"]:
                per_person[person].append(i)
    for person, held in sorted(per_person.items()):
        if len(held) > cfg.person_items:
            findings.append(("Over a WIP limit", f"{person} has {len(held)} items in progress, limit {cfg.person_items}"))
        days = sum(cfg.size(i["size"]).nominal for i in held if cfg.size(i["size"]))
        if days > cfg.person_days:
            findings.append(("Over a WIP limit", f"{person} has {days:g} nominal days in progress, limit {cfg.person_days:g}"))
    in_review = [i for i in items if i["status"] == "In review"]
    if len(in_review) > cfg.review_items:
        findings.append(("Over a WIP limit", f"{len(in_review)} items in review, limit {cfg.review_items}"))

    ready = [i for i in items if i["status"] == "Ready"]
    if len(ready) < cfg.team_size:
        findings.append(("Replenish Ready", f"Ready holds {len(ready)} items, fewer than the {cfg.team_size} people"))

    for i in items:
        if i["supertask"] and i["size"]:
            findings.append(("Rule broken", f"{label(i)}: a supertask has no Size, but this one is {i['size']}"))
        if not i["supertask"] and not i["size"] and i["status"] not in (None, "Backlog", "Done"):
            findings.append(("Rule broken", f"{label(i)}: a work item in {i['status']} needs a Size"))
        if i["size"] and not cfg.size(i["size"]):
            findings.append(("Rule broken", f"{label(i)}: Size {i['size']} is not in kanban.toml"))
        if i["status"] == "Ready" and i["assignees"]:
            findings.append(("Rule broken", f"{label(i)}: in Ready but assigned to {', '.join(i['assignees'])}"))
        if i["status"] == "In progress" and not i["assignees"]:
            findings.append(("Rule broken", f"{label(i)}: in progress with nobody assigned"))
        if i["status"] == "In progress" and not i["supertask"] and not i["start"]:
            findings.append(("Rule broken", f"{label(i)}: in progress with no start date, so its days cannot be counted"))
    return findings


def label(i):
    return f"{i['ref']} {i['title']}"


def report(findings, items, today):
    """The report as Markdown. The first line holds the date and nothing else."""
    lines = [f"Board on {today}"]
    if not findings:
        lines.append(f"\nNothing to report: {len(items)} items, nothing past its maximum, every limit held.")
    heading = None
    for head, line in findings:
        if head != heading:
            lines.append(f"\n### {head}\n")
            heading = head
        lines.append(f"- {line}")
    return "\n".join(lines) + "\n"


def post_if_changed(tok, cfg, body):
    """Comments on the report issue unless the last report says the same, date aside."""
    issues = request(tok, "GET", f"/repos/{cfg.repository}/issues?state=all&labels={REPORT_LABEL.replace(' ', '%20')}")
    if not issues:
        raise RuntimeError(f"{cfg.repository} has no issue labelled '{REPORT_LABEL}'; run setup_board.py")
    number = issues[0]["number"]
    owner, name = cfg.repository.split("/")
    data = graphql(tok, COMMENTS, {"owner": owner, "name": name, "number": number})
    mine = [
        c["body"] for c in data["repository"]["issue"]["comments"]["nodes"]
        if c["author"] and c["author"]["login"] == data["viewer"]["login"]
    ]
    if mine and mine[-1].splitlines()[1:] == body.splitlines()[1:]:
        return None
    request(tok, "POST", f"/repos/{cfg.repository}/issues/{number}/comments", {"body": body})
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="kanban.toml")
    parser.add_argument("--write", action="store_true", help="set Start date on items in progress without one")
    parser.add_argument("--comment", action="store_true", help="post the report on the report issue when it changed")
    args = parser.parse_args()
    cfg = load_config(args.config)
    today = dt.datetime.now(cfg.timezone).date()
    try:
        tok = token()
        project_id, start_field, items = fetch(tok, cfg)
        if args.write:
            for i in items:
                if i["status"] == "In progress" and not i["supertask"] and not i["has_start_date"]:
                    graphql(tok, SET_DATE, {"project": project_id, "item": i["id"], "field": start_field, "date": today.isoformat()})
                    print(f"Set Start date {today} on {label(i)}")
                    i["start"] = max(d for d in (i["start"], today) if d)
        findings = check(items, today, cfg)
        body = report(findings, items, today)
        print(body, end="")
        if args.comment:
            number = post_if_changed(tok, cfg, body)
            print(f"Posted on #{number}" if number else "Same as the last report, not posted")
            return 0
    except (RuntimeError, OSError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        print(f"board: {error}", file=sys.stderr)
        return 2
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
