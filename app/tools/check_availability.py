"""Tool: list bookable time slots in a window.

Demo implementation returns a deterministic synthetic schedule based on the
date so the agent can be tested end-to-end without an external calendar.
Swap `_fake_slots` with a real calendar client (Google Calendar, Cal.com,
NocoCal) for production.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

check_availability_spec: dict[str, Any] = {
    "name": "check_availability",
    "description": (
        "Return open appointment slots between two dates inclusive. "
        "Use this whenever the caller asks about availability or before "
        "calling `book_slot`. Times are returned in ISO 8601 with a "
        "timezone offset."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "from_date": {
                "type": "string",
                "format": "date",
                "description": "Start date (YYYY-MM-DD), inclusive.",
            },
            "to_date": {
                "type": "string",
                "format": "date",
                "description": "End date (YYYY-MM-DD), inclusive.",
            },
            "duration_minutes": {
                "type": "integer",
                "minimum": 15,
                "maximum": 240,
                "default": 30,
            },
        },
        "required": ["from_date", "to_date"],
    },
}


async def check_availability(args: dict[str, Any]) -> dict[str, Any]:
    from_date = date.fromisoformat(args["from_date"])
    to_date = date.fromisoformat(args["to_date"])
    duration = int(args.get("duration_minutes", 30))

    if to_date < from_date:
        return {"error": "to_date must be on or after from_date"}
    if (to_date - from_date).days > 14:
        return {"error": "window cannot exceed 14 days"}

    slots = []
    cur = from_date
    while cur <= to_date:
        slots.extend(_fake_slots(cur, duration))
        cur += timedelta(days=1)

    return {"duration_minutes": duration, "slots": slots}


def _fake_slots(d: date, duration: int) -> list[str]:
    if d.weekday() >= 5:
        return []
    times = [time(9, 0), time(11, 0), time(14, 0), time(16, 0)]
    return [
        datetime.combine(d, t).isoformat(timespec="minutes") + "+07:00"
        for t in times
        if (t.hour * 60 + t.minute + duration) <= 18 * 60
    ]
