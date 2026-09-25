# kanban-weeks

Kanban on a GitHub Projects board, run in weeks, with sizes that have a maximum, per-person work-in-progress limits, and a script that checks the board every working day. The rules are in [docs/kanban.md](docs/kanban.md), and the numbers in [kanban.toml](kanban.toml).

| File | What it does |
| --- | --- |
| `kanban.toml` | The project, the period, the team size, the limits, the sizes and the meetings. |
| `kanban/setup_board.py` | Makes the project's Status, Size, Week and Start date fields match `kanban.toml`, and creates the report issue. Dry run unless `--apply`. |
| `kanban/board.py` | Reports items past their maximum, broken limits, a short Ready column and broken rules. `--write` sets start dates, `--comment` posts the report. |
| `kanban/ics.py` | Writes the meetings to `meetings/calendar.ics`. |
| `.github/workflows/board.yml` | The reusable workflow that runs `board.py --write --comment`. |
| `.github/workflows/kanban.yml` | The caller: every weekday at 05:00 UTC, and by hand. |

Python 3.11 or later, standard library only.

## Start a project

1. Make a repository with **Use this template**, or copy `kanban.toml` and `.github/workflows/kanban.yml` into an existing one. The scripts are not needed in the project's repository: the workflow checks them out from here.
2. Make a project with the Board layout, and link the repository to it.
3. Fill in `kanban.toml`.
4. Run `python3 kanban/setup_board.py`, read what it would change, then run it again with `--apply`. It uses your `gh` login, which needs the project scope: `gh auth refresh -s project`.
5. In the project's Workflows page in the browser, since the API cannot create workflows:
   - Auto-add to project, filter `is:issue is:open`.
   - Item closed sets Done, and Item reopened sets Backlog.
6. Set up the machine account below.
7. Run `python3 kanban/ics.py` and share `meetings/calendar.ics`.

## The machine account

The scheduled check needs a token that can read and edit the project. GitHub's `GITHUB_TOKEN` "is scoped to the repository level and cannot access projects" ([docs](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/automating-projects-using-actions)). A fine-grained token has no permission for a user-owned project, so it has to be a classic token with the `project` scope, plus `repo` for a private repository. A classic token reaches every repository its account can, and anyone with write access to the repository can read a secret by pushing a workflow that prints it. So the token belongs to a separate account that can reach only this project.

1. Create a GitHub account for the project, and turn on two-factor authentication.
2. Invite it to the repository with the Read role. Read is enough to comment on issues.
3. Add it to the project with the Write role, under the project's Settings, Manage access.
4. Logged in as that account, create a classic token with the `project` scope, and `repo` if the repository is private. Give it an expiry that covers the period.
5. Add the token to the repository as the Actions secret `BOARD_TOKEN`.

## The report

`setup_board.py` creates a closed issue labelled `board report`. `board.py --comment` posts there when the findings differ from the last report, so a board that stays the same posts nothing. The issue stays closed, because the board's auto-add filter takes open issues only. Subscribe to the issue to get the reports.

## Versions

Releases are tags `vX.Y.Z`, and a caller pins one in its `uses:` line. The reusable workflow checks out the scripts at the same commit, so bumping that tag is the whole upgrade.

## Tests

`python3 -m unittest discover -s tests`

## License

Copyright (C) 2026 Alexander Öberg

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, version 3 only.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
