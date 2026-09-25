import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kanban"))
import board  # noqa: E402
import ics  # noqa: E402
import setup_board  # noqa: E402
from common import load_config, parse_config  # noqa: E402

CONFIG = load_config(Path(__file__).parent.parent / "kanban.toml")
MON = dt.date(2026, 9, 28)
TUE, WED, THU, FRI = (MON + dt.timedelta(days=n) for n in range(1, 5))
NEXT_MON = MON + dt.timedelta(weeks=1)


def item(ref, status, size=None, start=None, who=(), supertask=False):
    return {
        "id": ref, "ref": ref, "title": "t", "status": status, "size": size, "start": start,
        "has_start_date": bool(start), "assignees": list(who), "supertask": supertask,
    }


READY = [item(f"r{n}", "Ready", "Small") for n in range(CONFIG.team_size)]


def headings(items, today):
    return sorted({head for head, _ in board.check(items + READY, today, CONFIG)})


class WorkingDays(unittest.TestCase):
    def test_counts_both_ends(self):
        self.assertEqual(board.working_days(MON, MON), 1)

    def test_skips_the_weekend(self):
        self.assertEqual(board.working_days(FRI, NEXT_MON), 2)
        self.assertEqual(board.working_days(MON, NEXT_MON), 6)


class Maximum(unittest.TestCase):
    def test_small_at_its_maximum_and_one_past(self):
        self.assertEqual(headings([item("a", "In progress", "Small", MON, ["x"])], TUE), [])
        self.assertEqual(headings([item("a", "In progress", "Small", MON, ["x"])], WED), ["Sync needed"])

    def test_large_over_a_weekend(self):
        self.assertEqual(headings([item("a", "In progress", "Large", MON, ["x"])], FRI), [])
        self.assertEqual(headings([item("a", "In progress", "Large", MON, ["x"])], NEXT_MON), ["Sync needed"])

    def test_review_is_not_checked(self):
        self.assertEqual(headings([item("a", "In progress", "Medium", MON, ["x"])], THU), ["Sync needed"])
        self.assertEqual(headings([item("a", "In review", "Medium", MON, ["x"])], THU), [])
        self.assertEqual(headings([item("a", "In review", "Medium", None, ["x"])], THU), [])


class Limits(unittest.TestCase):
    def test_three_mediums_fit(self):
        self.assertEqual(headings([item(c, "In progress", "Medium", FRI, ["x"]) for c in "abc"], FRI), [])

    def test_four_items(self):
        self.assertEqual(headings([item(c, "In progress", "Small", FRI, ["x"]) for c in "abcd"], FRI), ["Over a WIP limit"])

    def test_nominal_days(self):
        held = [item("a", "In progress", "Large", FRI, ["x"]), item("b", "In progress", "Small", FRI, ["x"])]
        self.assertEqual(headings(held, FRI), ["Over a WIP limit"])

    def test_review_count(self):
        self.assertEqual(headings([item(c, "In review", "Small", FRI, ["x"]) for c in "abcd"], FRI), ["Over a WIP limit"])

    def test_ready_below_team_size(self):
        self.assertEqual(sorted({h for h, _ in board.check([], FRI, CONFIG)}), ["Replenish Ready"])


class Rules(unittest.TestCase):
    def test_supertask_with_a_size(self):
        self.assertEqual(headings([item("s", "Backlog", "Large", supertask=True)], FRI), ["Rule broken"])

    def test_supertask_in_progress_without_a_size(self):
        self.assertEqual(headings([item("s", "In progress", None, None, ["x"], supertask=True)], FRI), [])

    def test_ready_with_an_assignee(self):
        self.assertEqual(headings([item("r", "Ready", "Small", who=["x"])], FRI), ["Rule broken"])

    def test_in_progress_without_start(self):
        self.assertEqual(headings([item("a", "In progress", "Medium", None, ["x"])], FRI), ["Rule broken"])

    def test_unknown_size(self):
        self.assertEqual(headings([item("a", "Backlog", "Huge")], FRI), ["Rule broken"])

    def test_backlog_without_a_size(self):
        self.assertEqual(headings([item("b", "Backlog")], FRI), [])


class Report(unittest.TestCase):
    def test_first_line_is_only_the_date(self):
        text = board.report([("Sync needed", "#1 x")], [], FRI)
        self.assertEqual(text.splitlines()[0], f"Board on {FRI}")
        self.assertNotIn(str(FRI), "\n".join(text.splitlines()[1:]))


class Options(unittest.TestCase):
    existing = [
        {"id": "a", "name": "Todo", "color": "GRAY", "description": ""},
        {"id": "b", "name": "In Progress", "color": "YELLOW", "description": ""},
        {"id": "c", "name": "Done", "color": "ORANGE", "description": "Finished."},
    ]

    def test_keeps_ids_by_name_ignoring_case(self):
        desired = [("In progress", "YELLOW", "x"), ("Done", "ORANGE", "Finished.")]
        options, dropped, changed = setup_board.plan_options(self.existing, desired)
        self.assertEqual([o.get("id") for o in options], ["b", "c"])
        self.assertEqual(options[0]["name"], "In progress")
        self.assertEqual(dropped, ["Todo"])
        self.assertTrue(changed)

    def test_unchanged(self):
        desired = [(o["name"], o["color"], o["description"]) for o in self.existing]
        self.assertFalse(setup_board.plan_options(self.existing, desired)[2])

    def test_refuses_to_drop_a_held_option(self):
        fields = {"Status": {"id": "s", "options": self.existing}}
        with self.assertRaises(RuntimeError):
            setup_board.plan(CONFIG, "p", fields, {"Status": {"a"}})

    def test_size_descriptions(self):
        self.assertEqual(setup_board.size_options(CONFIG), [
            ("Small", "GREEN", "Nominal half a day. Maximum 2 working days in progress."),
            ("Medium", "YELLOW", "Nominal 1 day. Maximum 3 working days in progress."),
            ("Large", "ORANGE", "Nominal 3 days. Maximum 5 working days in progress. Bigger is a supertask."),
        ])


class Calendar(unittest.TestCase):
    def config(self, meeting):
        text = (Path(__file__).parent.parent / "kanban.toml").read_text()
        text = text.split("[[meetings]]")[0] + meeting
        return parse_config(text.replace("start = 2026-01-05", "start = 2026-09-14"))

    def test_weekly_across_summer_time(self):
        cfg = self.config('[[meetings]]\nid = "sync"\nname = "Sync, weekly; mandatory"\nweekday = "MO"\n'
                          'time = 09:00:00\nminutes = 60\nfirst_week = 3\n')
        text = ics.calendar(cfg)
        self.assertNotIn("\n", text.replace("\r\n", ""))
        self.assertIn("DTSTART;TZID=Europe/Stockholm:20260928T090000\r\n", text)
        self.assertIn("DTEND;TZID=Europe/Stockholm:20260928T100000\r\n", text)
        # Week 12's Monday is 2026-11-30, in winter time, so 09:00 is 08:00 UTC.
        self.assertIn("RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20261130T080000Z\r\n", text)
        self.assertIn("SUMMARY:Sync\\, weekly\\; mandatory\r\n", text)
        self.assertEqual(text, ics.calendar(cfg))

    def test_every_other_week(self):
        cfg = self.config('[[meetings]]\nid = "retro"\nname = "Retro"\nweekday = "FR"\n'
                          'time = 14:00:00\nminutes = 60\nfirst_week = 3\nevery = 2\n')
        text = ics.calendar(cfg)
        self.assertIn("DTSTART;TZID=Europe/Stockholm:20261002T140000\r\n", text)
        # Weeks 3, 5, 7, 9, 11: the last is Friday 2026-11-27, 13:00 UTC.
        self.assertIn("RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=FR;UNTIL=20261127T130000Z\r\n", text)

    def test_long_lines_fold(self):
        cfg = self.config('[[meetings]]\nid = "m"\nname = "M"\nweekday = "MO"\ntime = 09:00:00\nminutes = 15\n'
                          f'description = "{"å" * 100}"\n')
        for line in ics.calendar(cfg).split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)
        unfolded = ics.calendar(cfg).replace("\r\n ", "")
        self.assertIn("DESCRIPTION:" + "å" * 100 + "\r\n", unfolded)


if __name__ == "__main__":
    unittest.main()
