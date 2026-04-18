"""Tool: reserve a slot returned by `check_availability`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

book_slot_spec: dict[str, Any] = {
    "name": "book_slot",
    "description": (
        "Book a specific appointment slot. Only call after `check_availability` "
        "and after the caller has explicitly confirmed the time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "starts_at": {
                "type": "string",
                "format": "date-time",
                "description": (
                    "ISO 8601 timestamp of the slot, must match a value "
                    "returned by check_availability."
                ),
            },
            "duration_minutes": {
                "type": "integer",
                "minimum": 15,
                "maximum": 240,
            },
            "customer_name": {"type": "string", "minLength": 1, "maxLength": 100},
            "customer_phone": {
                "type": "string",
                "description": "E.164 phone (e.g. +66812345678).",
                "pattern": r"^\+[1-9]\d{6,14}$",
            },
            "notes": {"type": "string", "maxLength": 500},
        },
        "required": ["starts_at", "duration_minutes", "customer_name", "customer_phone"],
    },
}


async def book_slot(args: dict[str, Any]) -> dict[str, Any]:
    try:
        starts_at = datetime.fromisoformat(args["starts_at"])
    except ValueError:
        return {"error": "starts_at is not a valid ISO 8601 timestamp"}

    booking_id = f"bk_{uuid.uuid4().hex[:10]}"
    return {
        "booking_id": booking_id,
        "status": "confirmed",
        "starts_at": starts_at.isoformat(),
        "duration_minutes": int(args["duration_minutes"]),
        "customer_name": args["customer_name"],
        "customer_phone": args["customer_phone"],
        "notes": args.get("notes", ""),
    }
