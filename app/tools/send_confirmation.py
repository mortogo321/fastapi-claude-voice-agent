"""Tool: send a booking confirmation SMS via Twilio.

Falls back to a no-op log entry if Twilio creds are not configured, so the
demo runs without spending SMS quota.
"""

from __future__ import annotations

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


async def send_confirmation(args: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    to = args["to_phone"]
    body = args["message"]

    if not (
        settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number
    ):
        log.warning("sms.skipped.no_credentials", to=to)
        return {"status": "skipped", "reason": "twilio_not_configured", "to": to}

    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    msg = client.messages.create(
        from_=settings.twilio_from_number,
        to=to,
        body=body,
    )
    log.info("sms.sent", sid=msg.sid, to=to)
    return {"status": "sent", "sid": msg.sid, "to": to}
