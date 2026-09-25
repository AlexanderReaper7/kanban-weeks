# Kanban in weeks

Provenance: user. The rules come from the teaterihuskvarna project's decision record 0018 of 2026-09-25, where the user decided them or accepted an agent's proposal. An agent rewrote them here without that project's names and history. The numbers are the defaults in [kanban.toml](../kanban.toml), and a project sets its own there.

## Flow, in weeks

The team works in continuous flow, not sprints. A week, Monday to Sunday, is a unit of the calendar and not a commitment. The board's `Week` field has one iteration per week of the period.

## Meetings

| Meeting | When | Length |
| --- | --- | --- |
| Week-start sync | Every Monday morning, mandatory | 1 hour allocated |
| Product owner meeting | Weekly | At least 1 hour |
| Retrospective | Every second Friday | Set by the team |
| Sync | When anyone calls it, or when an item passes its maximum | At most 15 minutes |

- The week-start sync is also the weekly refill of Ready.
- The product owner meeting is also the review.
- A sync ends when every problem raised has an owner and a next step, and after that only the people involved stay.
- Replenishment: the product owner orders the backlog at the product owner meeting. Besides the Monday refill, the team breaks down the next piece of work whenever Ready holds fewer items than there are people.

`kanban/ics.py` writes the meetings in `kanban.toml` to a calendar file.

## Work items and sizes

A work item is an issue without sub-issues, and needs a size. An issue with sub-issues is a supertask and has no size. Anything larger than the largest size is a supertask.

| Size | Nominal | Maximum |
| --- | --- | --- |
| Small | Half a day | 2 working days in progress |
| Medium | 1 day | 3 working days in progress |
| Large | 3 days | 5 working days in progress |

- Time is counted in days, not hours, because hours are not equally productive over a day.
- A day counts if the item was In progress on it, and both the first and the last day count. Weekends do not count.
- Review is separate: only days In progress count toward the maximum.
- An item passing its maximum calls a sync.
- An item gets its size when it enters Ready, before anyone is assigned to it, and the size does not change after it leaves Ready.
- A size is the same whoever does the work.
- An item may enter Ready when it has a size, acceptance criteria, and no open question to the product owner.
- Work under about an hour gets no size. It goes as a checklist line in the issue it belongs to.

## Work-in-progress limits

- At most 3 items In progress per person.
- At most 3 days of nominal time In progress per person.
- At most 3 items In review for the whole team.

## The board

| Column | Rule |
| --- | --- |
| Backlog | Not ready to start. Waits in the order the product owner sets. |
| Ready | May be pulled. Needs a Size, acceptance criteria and no open question to the product owner. Nobody assigned yet. |
| In progress | Being worked on, within the per-person limits. |
| In review | Waiting for review, within the team's limit. |
| Done | Finished. |

`kanban/setup_board.py` writes each rule into its column's description, so the board shows it. `kanban/board.py` checks the board every working day and reports what breaks a rule.

A GitHub column limit counts one column for the whole team and only warns, so it cannot hold a per-person limit. It is still worth setting as the ceiling the per-person limits allow, on every board view, by hand, since each view keeps its own limits. Neither API can set them. Checked on 2026-09-25: GraphQL's `updateProjectV2View` takes a name, a layout, a filter and the visible fields, and the REST API's OpenAPI description has no column limit anywhere.

The limits:

| Column | Column limit |
| --- | --- |
| In progress | `team.size` × `limits.person_items` |
| In review | `limits.review_items` |
| Backlog, Ready, Done | None |

Set them again when `kanban.toml` changes either number.
