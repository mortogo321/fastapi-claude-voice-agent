"""Tool: send a booking confirmation SMS via Twilio.

Falls back to a no-op log entry if Twilio creds are not configured, so the
demo runs without spending SMS quota. The blocking Twilio client is moved
to a thread via `asyncio.to_thread` so we don't block the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)

send_confirmation_spec: dict[str, Any] = {
    "name": "send_confirmation_sms",
    "description": (
        "Send an SMS confirming a booking. Only call after `book_slot` "
        "succeeds. Keep the message under 160 characters."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to_phone": {
                "type": "string",
                "pattern": r"^\+[1-9]\d{6,14}$",
            },
            "message": {"type": "string", "minLength": 1, "maxLength": 160},
        },
        "required": ["to_phone", "message"],
    },
}


def _send_sync(account_sid: str, auth_token: str, from_number: str, to: str, body: str) -> str:
    from twilio.rest import Client  # type: ignore[import-untyped]

    client = Client(account_sid, auth_token)
    msg = client.messages.create(from_=from_number, to=to, body=body)
    return str(msg.sid)


async def send_confirmation(args: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    to = args["to_phone"]
    body = args["message"]

    if not settings.twilio_configured:
        log.warning("sms.skipped.no_credentials", to=to)
        return {"status": "skipped", "reason": "twilio_not_configured", "to": to}

    sid = await asyncio.to_thread(
        _send_sync,
        settings.twilio_account_sid.get_secret_value(),
        settings.twilio_auth_token.get_secret_value(),
        settings.twilio_from_number,
        to,
        body,
    )
    log.info("sms.sent", sid=sid, to=to)
    return {"status": "sent", "sid": sid, "to": to}
