#!/usr/bin/env python3
"""Writes the meetings in kanban.toml as an iCalendar file.

Each meeting recurs weekly, or every `every` weeks, from `first_week` to the
last week of the period. Times are local to the project's timezone, so a
meeting stays at 09:00 across the change to and from summer time.

RFC 5545 requires every TZID to have a VTIMEZONE, and the standard library
cannot write one, so only the timezones in VTIMEZONES are supported. The output
is deterministic: running it twice on the same config gives the same file.

Run: python3 kanban/ics.py [--config kanban.toml] [--output meetings/calendar.ics]
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_config  # noqa: E402

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

# Central European Time, as the EU has set it since 1996.
CET = """BEGIN:VTIMEZONE
TZID:{tzid}
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""

VTIMEZONES = {name: CET for name in ("Europe/Stockholm", "Europe/Oslo", "Europe/Copenhagen", "Europe/Berlin")}


def escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold(line):
    """Splits a content line into 75-octet pieces, as RFC 5545 section 3.1 requires."""
    data = line.encode()
    pieces, limit = [], 75
    while len(data) > limit:
        cut = limit
        while (data[cut] & 0xC0) == 0x80:  # never inside a UTF-8 sequence
            cut -= 1
        pieces.append(data[:cut])
        data, limit = data[cut:], 74
    pieces.append(data)
    return "\r\n ".join(p.decode() for p in pieces)


def occurrences(cfg, meeting):
    """The first and last date the meeting falls on within the period."""
    offset = WEEKDAYS.index(meeting.weekday)
    weeks = range(meeting.first_week, cfg.weeks + 1, meeting.every)
    if not weeks:
        raise ValueError(f"{meeting.id}: first_week {meeting.first_week} is after the last week")
    day = dt.timedelta(days=offset)
    return cfg.week_start(weeks[0]) + day, cfg.week_start(weeks[-1]) + day


def calendar(cfg):
    tzid = cfg.timezone.key
    if tzid not in VTIMEZONES:
        raise ValueError(f"no VTIMEZONE for {tzid}; add it to VTIMEZONES in {Path(__file__).name}")
    name = cfg.repository.split("/")[-1]
    stamp = f"{cfg.start:%Y%m%d}T000000Z"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{name}//kanban-weeks//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{escape(cfg.calendar_name)}",
        f"X-WR-TIMEZONE:{tzid}",
        *VTIMEZONES[tzid].format(tzid=tzid).splitlines(),
    ]
    for m in cfg.meetings:
        first, last = occurrences(cfg, m)
        start = dt.datetime.combine(first, m.time)
        until = dt.datetime.combine(last, m.time, tzinfo=cfg.timezone).astimezone(dt.UTC)
        rule = f"FREQ=WEEKLY;{f'INTERVAL={m.every};' if m.every > 1 else ''}BYDAY={m.weekday};UNTIL={until:%Y%m%dT%H%M%SZ}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{m.id}@{name}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={tzid}:{start:%Y%m%dT%H%M%S}",
            f"DTEND;TZID={tzid}:{start + dt.timedelta(minutes=m.minutes):%Y%m%dT%H%M%S}",
            f"RRULE:{rule}",
            f"SUMMARY:{escape(m.name)}",
            *([f"DESCRIPTION:{escape(m.description)}"] if m.description else []),
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(fold(line) + "\r\n" for line in lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="kanban.toml")
    parser.add_argument("--output", default="meetings/calendar.ics")
    args = parser.parse_args()
    text = calendar(load_config(args.config))
    Path(args.output).write_bytes(text.encode())
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
