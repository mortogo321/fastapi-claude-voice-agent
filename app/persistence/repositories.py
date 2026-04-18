"""Async repositories — narrow façade over SQLAlchemy queries."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.persistence.models import CallSession, ToolCallRecord, TranscriptTurn


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, call_sid: str, from_number: str) -> CallSession:
        session = CallSession(call_sid=call_sid, from_number=from_number)
        self._db.add(session)
        await self._db.commit()
        return session

    async def mark_ended(self, call_sid: str) -> None:
        result = await self._db.execute(select(CallSession).where(CallSession.call_sid == call_sid))
        session = result.scalar_one_or_none()
        if session is None:
            return
        session.ended_at = datetime.now(UTC)
        await self._db.commit()

    async def add_turn(
        self,
        call_sid: str,
        role: str,
        text: str,
        latency_ms: int | None = None,
    ) -> None:
        self._db.add(
            TranscriptTurn(
                call_sid=call_sid,
                role=role,
                text=text,
                latency_ms=latency_ms,
            )
        )
        await self._db.commit()

    async def add_tool_call(
        self,
        call_sid: str,
        tool_name: str,
        input_json: dict[str, Any],
        output_json: dict[str, Any],
        latency_ms: int | None,
    ) -> None:
        self._db.add(
            ToolCallRecord(
                call_sid=call_sid,
                tool_name=tool_name,
                input_json=input_json,
                output_json=output_json,
                latency_ms=latency_ms,
            )
        )
        await self._db.commit()

    async def get_with_turns(self, call_sid: str) -> CallSession | None:
        result = await self._db.execute(
            select(CallSession)
            .options(
                selectinload(CallSession.turns),
                selectinload(CallSession.tool_calls),
            )
            .where(CallSession.call_sid == call_sid)
        )
        return result.scalar_one_or_none()
