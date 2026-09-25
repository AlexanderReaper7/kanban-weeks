#!/usr/bin/env python3
"""Makes a project's fields match kanban.toml, and creates the report issue.

- Status: the options Backlog, Ready, In progress, In review and Done, each
  with its rule as the description.
- Size: one option per size, with its nominal time and maximum.
- Week: one iteration per week of the period, "Week 1" onwards.
- Start date: the date board.py counts days from.
- The repository's closed issue labelled "board report", which board.py
  --comment posts on.

An existing option keeps its id when its name matches, ignoring case, so the
items holding it keep their value. The script refuses to drop an option some
item holds, or to change the iterations while any item has a Week. It changes
no view and no workflow: the API cannot create either.

Without --apply it only prints what it would change.

Run: python3 kanban/setup_board.py [--config kanban.toml] [--apply]
"""
import argparse
import sys
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from board import REPORT_LABEL  # noqa: E402
from common import graphql, load_config, request, token  # noqa: E402

FIELDS = """
query($owner: String!, $number: Int!, $after: String) {
  repositoryOwner(login: $owner) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        fields(first: 50) {
          nodes {
            ... on ProjectV2FieldCommon { id name dataType }
            ... on ProjectV2SingleSelectField { options { id name color description } }
            ... on ProjectV2IterationField {
              configuration {
                iterations { title startDate duration }
                completedIterations { title startDate duration }
              }
            }
          }
        }
        items(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes {
            fieldValues(first: 30) {
              nodes {
                ... on ProjectV2ItemFieldSingleSelectValue { optionId field { ... on ProjectV2FieldCommon { name } } }
                ... on ProjectV2ItemFieldIterationValue { field { ... on ProjectV2FieldCommon { name } } }
              }
            }
          }
        }
      }
    }
  }
}
"""

CREATE = """
mutation($input: CreateProjectV2FieldInput!) {
  createProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2FieldCommon { id } } }
}
"""

UPDATE = """
mutation($input: UpdateProjectV2FieldInput!) {
  updateProjectV2Field(input: $input) { projectV2Field { ... on ProjectV2FieldCommon { id } } }
}
"""

STATUS_COLORS = {"Backlog": "GREEN", "Ready": "BLUE", "In progress": "YELLOW", "In review": "PURPLE", "Done": "ORANGE"}


def status_options(cfg):
    po = cfg.product_owner
    return [
        ("Backlog", f"Not ready to start. Waits here in the order {po} sets."),
        ("Ready", f"May be pulled. Needs a Size, acceptance criteria and no open question to {po}. Nobody assigned yet."),
        ("In progress", f"Being worked on. Per person: at most {cfg.person_items} items and {cfg.person_days:g} nominal days."),
        ("In review", f"Waiting for review. At most {cfg.review_items} items for the whole team."),
        ("Done", "Finished."),
    ]


def nominal_text(days):
    if days == 0.5:
        return "half a day"
    return f"{days:g} day" + ("" if days == 1 else "s")


def size_options(cfg):
    options = []
    for n, size in enumerate(cfg.sizes):
        text = f"Nominal {nominal_text(size.nominal)}. Maximum {size.maximum} working days in progress."
        if n == len(cfg.sizes) - 1:
            text += " Bigger is a supertask."
        options.append((size.name, size.color, text))
    return options


def plan_options(existing, desired):
    """Matches desired (name, color, description) options to existing ones by name.

    Returns the option input for the API, the names of existing options that
    would be dropped, and whether anything differs.
    """
    by_name = {o["name"].casefold(): o for o in existing}
    options, changed = [], len(existing) != len(desired)
    for name, color, description in desired:
        old = by_name.pop(name.casefold(), None)
        option = {"name": name, "color": color, "description": description}
        if old:
            option["id"] = old["id"]
            changed |= (old["name"], old["color"], old["description"]) != (name, color, description)
        else:
            changed = True
        options.append(option)
    return options, [o["name"] for o in by_name.values()], changed


def weeks(cfg):
    return [
        {"title": f"Week {w}", "startDate": cfg.week_start(w).isoformat(), "duration": 7}
        for w in range(1, cfg.weeks + 1)
    ]


def fetch(tok, cfg):
    fields, used, after = None, {}, None
    while True:
        owner = graphql(tok, FIELDS, {"owner": cfg.owner, "number": cfg.number, "after": after})["repositoryOwner"]
        project = owner and owner.get("projectV2")
        if not project:
            raise RuntimeError(f"no project {cfg.owner}/{cfg.number}")
        fields = project["id"], {f["name"]: f for f in project["fields"]["nodes"] if f}
        for item in project["items"]["nodes"]:
            for value in item["fieldValues"]["nodes"]:
                if value and value.get("field"):
                    used.setdefault(value["field"]["name"], set()).add(value.get("optionId"))
        page = project["items"]["pageInfo"]
        if not page["hasNextPage"]:
            return fields[0], fields[1], used
        after = page["endCursor"]


def plan(cfg, project_id, fields, used):
    """Returns (description, mutation, input) steps, or raises when a step would lose data."""
    steps = []
    selects = [
        ("Status", [(n, STATUS_COLORS[n], d) for n, d in status_options(cfg)]),
        ("Size", size_options(cfg)),
    ]
    for name, desired in selects:
        field = fields.get(name)
        if not field:
            steps.append((f"{name}: create", CREATE, {
                "projectId": project_id, "dataType": "SINGLE_SELECT", "name": name,
                "singleSelectOptions": plan_options([], desired)[0],
            }))
            continue
        options, dropped, changed = plan_options(field["options"], desired)
        held = [o["name"] for o in field["options"] if o["name"] in dropped and o["id"] in used.get(name, set())]
        if held:
            raise RuntimeError(f"{name}: items hold {', '.join(held)}, which kanban.toml does not have; move them first")
        if changed:
            what = f"set options {', '.join(o['name'] for o in options)}"
            if dropped:
                what += f", dropping {', '.join(dropped)}"
            steps.append((f"{name}: {what}", UPDATE, {"fieldId": field["id"], "singleSelectOptions": options}))

    desired_weeks = weeks(cfg)
    iteration = {"startDate": cfg.start.isoformat(), "duration": 7, "iterations": desired_weeks}
    field = fields.get("Week")
    if not field:
        steps.append((f"Week: create {cfg.weeks} weeks from {cfg.start}", CREATE, {
            "projectId": project_id, "dataType": "ITERATION", "name": "Week", "iterationConfiguration": iteration,
        }))
    else:
        config = field["configuration"]
        have = sorted(config["iterations"] + config["completedIterations"], key=lambda i: i["startDate"])
        if have != desired_weeks:
            if "Week" in used:
                raise RuntimeError("Week: the iterations differ from kanban.toml, and items have a Week; change them in the browser")
            steps.append((f"Week: replace with {cfg.weeks} weeks from {cfg.start}", UPDATE, {
                "fieldId": field["id"], "iterationConfiguration": iteration,
            }))

    if "Start date" not in fields:
        steps.append(("Start date: create", CREATE, {"projectId": project_id, "dataType": "DATE", "name": "Start date"}))
    return steps


def report_issue(tok, cfg, apply):
    label = urllib.parse.quote(REPORT_LABEL)
    try:
        request(tok, "GET", f"/repos/{cfg.repository}/labels/{label}")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        print(f"Label '{REPORT_LABEL}': create")
        if apply:
            request(tok, "POST", f"/repos/{cfg.repository}/labels", {
                "name": REPORT_LABEL, "color": "ededed", "description": "board.py posts its daily report here",
            })
    issues = request(tok, "GET", f"/repos/{cfg.repository}/issues?state=all&labels={label}")
    if issues:
        return
    print("Report issue: create, then close so the board's auto-add skips it")
    if apply:
        issue = request(tok, "POST", f"/repos/{cfg.repository}/issues", {
            "title": "Board report",
            "body": "board.py posts here when the board's findings change. Subscribe to get them. "
                    "The issue stays closed so the board does not add it as a card.",
            "labels": [REPORT_LABEL],
        })
        request(tok, "PATCH", f"/repos/{cfg.repository}/issues/{issue['number']}", {"state": "closed"})
        print(f"Report issue: #{issue['number']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="kanban.toml")
    parser.add_argument("--apply", action="store_true", help="make the changes instead of printing them")
    args = parser.parse_args()
    cfg = load_config(args.config)
    try:
        tok = token()
        project_id, fields, used = fetch(tok, cfg)
        steps = plan(cfg, project_id, fields, used)
        for what, mutation, value in steps:
            print(what)
            if args.apply:
                graphql(tok, mutation, {"input": value})
        if not steps:
            print("Fields: nothing to change")
        report_issue(tok, cfg, args.apply)
    except (RuntimeError, OSError) as error:
        print(f"setup: {error}", file=sys.stderr)
        return 2
    if not args.apply:
        print("Dry run. Run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
