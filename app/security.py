"""Webhook signature validation.

Twilio signs every webhook request with HMAC-SHA1 over the full URL +
sorted POST body params, using the account auth token as the shared secret.
We verify it before any side effect — otherwise anyone who guesses the URL
can spin up arbitrary calls against our Anthropic key.

In dev (or when `TWILIO_VALIDATE_SIGNATURE=false` is set explicitly) we
skip validation so local curl testing works; production always validates.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.config import Settings
from app.logging import get_logger

log = get_logger(__name__)


async def validate_twilio_signature(request: Request, settings: Settings) -> None:
    if not settings.twilio_validate_signature:
        return

    auth_token = settings.twilio_auth_token.get_secret_value()
    if not auth_token:
        if settings.is_production:
            log.error("twilio.signature.missing_token")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="twilio signature validation not configured",
            )
        # Non-prod without a token: skip gracefully.
        return

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        log.warning("twilio.signature.missing_header")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing signature")

    url = _public_url(request, settings)
    params = dict((await request.form()).multi_items())

    from twilio.request_validator import RequestValidator  # type: ignore[import-untyped]

    validator = RequestValidator(auth_token)
    if not validator.validate(url, params, signature):
        log.warning("twilio.signature.invalid", url=url)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid signature")


def _public_url(request: Request, settings: Settings) -> str:
    """Twilio signs against the public URL it posted to, not the backend URL.

    Behind ngrok, Cloudflare Tunnel, or a load balancer, `request.url` will
    be the internal one. We rebuild from `PUBLIC_BASE_URL` + path so the
    verification hash matches what Twilio signed.
    """
    path = request.url.path
    query = request.url.query
    base = settings.public_base_url.rstrip("/")
    return f"{base}{path}" + (f"?{query}" if query else "")
