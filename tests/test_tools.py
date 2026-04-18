from __future__ import annotations

from app.tools.book_slot import book_slot
from app.tools.check_availability import check_availability
from app.tools.registry import build_default_registry


def test_default_registry_exposes_three_tools():
    registry = build_default_registry()
    names = {spec["name"] for spec in registry.tool_specs()}
    assert names == {"check_availability", "book_slot", "send_confirmation_sms"}


def test_tool_specs_have_anthropic_shape():
    for spec in build_default_registry().tool_specs():
        assert "name" in spec
        assert "description" in spec
        assert "input_schema" in spec
        assert spec["input_schema"]["type"] == "object"


async def test_check_availability_returns_weekday_slots():
    result = await check_availability(
        {"from_date": "2026-04-20", "to_date": "2026-04-20", "duration_minutes": 30}
    )
    assert "slots" in result
    assert len(result["slots"]) > 0
    for slot in result["slots"]:
        assert slot.endswith("+07:00")


async def test_check_availability_rejects_inverted_window():
    result = await check_availability({"from_date": "2026-04-20", "to_date": "2026-04-19"})
    assert "error" in result


async def test_check_availability_rejects_window_over_two_weeks():
    result = await check_availability({"from_date": "2026-04-01", "to_date": "2026-05-01"})
    assert "error" in result


async def test_check_availability_skips_weekend():
    # 2026-04-25 is a Saturday
    result = await check_availability({"from_date": "2026-04-25", "to_date": "2026-04-25"})
    assert result["slots"] == []


async def test_book_slot_returns_confirmation():
    result = await book_slot(
        {
            "starts_at": "2026-04-21T11:00:00+07:00",
            "duration_minutes": 30,
            "customer_name": "Test User",
            "customer_phone": "+66812345678",
        }
    )
    assert result["status"] == "confirmed"
    assert result["booking_id"].startswith("bk_")


async def test_book_slot_rejects_bad_timestamp():
    result = await book_slot(
        {
            "starts_at": "not-a-time",
            "duration_minutes": 30,
            "customer_name": "Test",
            "customer_phone": "+66812345678",
        }
    )
    assert "error" in result


async def test_registry_execute_unknown_tool():
    registry = build_default_registry()
    result = await registry.execute("nope", {})
    assert "error" in result
