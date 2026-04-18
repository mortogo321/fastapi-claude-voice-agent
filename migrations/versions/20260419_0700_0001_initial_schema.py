"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-19 07:00:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_sessions",
        sa.Column("call_sid", sa.String(length=64), primary_key=True),
        sa.Column("from_number", sa.String(length=32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_call_sessions_from_number", "call_sessions", ["from_number"])

    op.create_table(
        "transcript_turns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "call_sid",
            sa.String(length=64),
            sa.ForeignKey("call_sessions.call_sid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_transcript_turns_call_sid", "transcript_turns", ["call_sid"])

    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "call_sid",
            sa.String(length=64),
            sa.ForeignKey("call_sessions.call_sid", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("input_json", sa.JSON, nullable=False),
        sa.Column("output_json", sa.JSON, nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tool_calls_call_sid", "tool_calls", ["call_sid"])


def downgrade() -> None:
    op.drop_index("ix_tool_calls_call_sid", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_index("ix_transcript_turns_call_sid", table_name="transcript_turns")
    op.drop_table("transcript_turns")
    op.drop_index("ix_call_sessions_from_number", table_name="call_sessions")
    op.drop_table("call_sessions")
