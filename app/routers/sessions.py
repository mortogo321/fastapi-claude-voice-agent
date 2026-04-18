"""Inspect persisted call sessions for replay / debugging."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.db import get_session
from app.persistence.repositories import SessionRepository

router = APIRouter()


@router.get("/{call_sid}")
async def get_call_session(
    call_sid: str,
    db: AsyncSession = Depends(get_session),
) -> dict:
    repo = SessionRepository(db)
    session = await repo.get_with_turns(call_sid)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "call_sid": session.call_sid,
        "from_number": session.from_number,
        "started_at": session.started_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "turns": [
            {
                "role": turn.role,
                "text": turn.text,
                "at": turn.created_at.isoformat(),
                "latency_ms": turn.latency_ms,
            }
            for turn in session.turns
        ],
        "tool_calls": [
            {
                "name": tc.tool_name,
                "input": tc.input_json,
                "output": tc.output_json,
                "latency_ms": tc.latency_ms,
                "at": tc.created_at.isoformat(),
            }
            for tc in session.tool_calls
        ],
    }
