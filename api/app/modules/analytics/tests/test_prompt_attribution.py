"""Prompt-level cost attribution: write path, backfill, and analytics roll-up.

A system prompt identifies the downstream application more often than not, so
``llm_usage_logs.prompt_hash`` is a SHA-256 over the system prompt only. These
tests pin three things: the hash is stable and ignores incidental whitespace,
older rows can be recovered from the stored encoded prompt, and the analytics
roll-up groups on it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.prompt_identity import (
    PROMPT_MODEL_PREFIX,
    PROMPT_SEGMENT_SEPARATOR,
    compute_prompt_hash,
    system_prompt_from_prompt_text,
)
from app.modules.analytics.analytics_service import AnalyticsService
from app.modules.llm_usage.llm_usage_model import LlmUsageLog
from app.modules.llm_usage.llm_usage_service import LlmUsageService

SUPPORT_BOT = "You are a helpful support agent for Acme. Never mention refunds."
SALES_BOT = "You answer pricing questions for Acme using the public price list."


def _encode(messages: list[tuple[str, str]], model: str = "gpt-4o-mini") -> str:
    """Mirror of the encoding used by ``LlmRouterService._prepare_prompts``."""
    segments = [f"{role}: {content}" for role, content in messages]
    joined = PROMPT_SEGMENT_SEPARATOR.join(segments)
    return f"{PROMPT_MODEL_PREFIX}{model}|{joined}"


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        yield db
    finally:
        db.close()


# --- hashing -----------------------------------------------------------------


def test_hash_is_stable_and_whitespace_insensitive():
    h = compute_prompt_hash(SUPPORT_BOT)
    assert h is not None
    assert len(h) == 64
    assert h == compute_prompt_hash(SUPPORT_BOT)
    assert h == compute_prompt_hash(f"  {SUPPORT_BOT}\n\n")
    assert h == compute_prompt_hash(
        "You are a helpful support agent for Acme.  Never mention refunds."
    )
    assert h != compute_prompt_hash(SALES_BOT)


def test_hash_is_none_without_a_system_prompt():
    assert compute_prompt_hash(None) is None
    assert compute_prompt_hash("") is None
    assert compute_prompt_hash("   \n\t ") is None


# --- backfill recovery -------------------------------------------------------


def test_system_prompt_survives_a_round_trip():
    encoded = _encode(
        [
            ("system", SUPPORT_BOT),
            ("user", "where is my order?"),
            ("assistant", "Checking now."),
            ("user", "thanks"),
        ]
    )
    assert system_prompt_from_prompt_text(encoded) == SUPPORT_BOT
    assert compute_prompt_hash(system_prompt_from_prompt_text(encoded)) == (
        compute_prompt_hash(SUPPORT_BOT)
    )


def test_multiple_system_messages_are_concatenated():
    encoded = _encode([("system", "part one"), ("system", "part two"), ("user", "hi")])
    recovered = system_prompt_from_prompt_text(encoded)
    assert recovered is not None
    assert recovered.splitlines() == ["part one", "part two"]
    assert compute_prompt_hash(recovered) == compute_prompt_hash("part one\npart two")


def test_prompt_without_system_message_is_not_attributed():
    assert system_prompt_from_prompt_text(_encode([("user", "hi")])) is None
    assert system_prompt_from_prompt_text(None) is None
    assert system_prompt_from_prompt_text("") is None


def test_ambiguous_encoding_is_rejected_rather_than_guessed():
    """Content containing the segment separator fabricates segments; skip those."""
    poisoned = _encode([("system", "be nice"), ("user", "a|\nb")])
    assert system_prompt_from_prompt_text(poisoned) is None


def test_backfill_fills_null_hashes_and_skips_unrecoverable_rows(session):
    service = LlmUsageService(db_session=session)
    session.add_all(
        [
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                input_text=_encode([("system", SUPPORT_BOT), ("user", "hi")]),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                input_text=_encode([("system", SALES_BOT), ("user", "price?")]),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                input_text=_encode([("user", "no system prompt")]),
            ),
        ]
    )
    session.commit()

    scanned, updated, skipped = service.backfill_prompt_hashes(batch_size=2)

    assert (scanned, updated, skipped) == (3, 2, 1)
    hashes = {
        row.prompt_hash
        for row in session.query(LlmUsageLog).order_by(LlmUsageLog.id).all()
    }
    assert hashes == {
        compute_prompt_hash(SUPPORT_BOT),
        compute_prompt_hash(SALES_BOT),
        None,
    }

    # Re-running must not re-scan attributed rows.
    assert service.backfill_prompt_hashes(batch_size=2) == (1, 0, 1)


# --- analytics roll-up -------------------------------------------------------


def test_prompt_breakdown_groups_cost_per_system_prompt(session):
    start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    support = compute_prompt_hash(SUPPORT_BOT)
    sales = compute_prompt_hash(SALES_BOT)

    session.add_all(
        [
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                prompt_hash=support,
                total_tokens=100,
                cost=0.4,
                tenant_id="acme",
                created_at=start + timedelta(minutes=1),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                prompt_hash=support,
                total_tokens=50,
                cost=0.1,
                tenant_id="acme",
                created_at=start + timedelta(minutes=2),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                prompt_hash=sales,
                total_tokens=20,
                cost=0.05,
                tenant_id="acme",
                created_at=start + timedelta(minutes=3),
            ),
            LlmUsageLog(
                service_name="openai",
                model="gpt-4o-mini",
                caller_service_name="tests",
                prompt_hash=None,
                total_tokens=5,
                cost=0.01,
                tenant_id="acme",
                created_at=start + timedelta(minutes=4),
            ),
            LlmUsageLog(
                service_name="gemini",
                model="gemini-2.0-flash",
                caller_service_name="tests",
                prompt_hash=sales,
                total_tokens=999,
                cost=9.0,
                tenant_id="other",
                created_at=start + timedelta(minutes=5),
            ),
        ]
    )
    session.commit()

    breakdown = (
        AnalyticsService(session)
        .get_dashboard_data(start_time=start, end_time=end, tenant_id="acme")
        .prompt_breakdown
    )

    by_hash = {row.prompt_hash: row for row in breakdown}
    assert set(by_hash) == {support, sales, None}

    # Most expensive first, so the biggest spender is the first row.
    assert breakdown[0].prompt_hash == support
    assert by_hash[support].requests == 2
    assert by_hash[support].total_tokens == 150
    assert by_hash[support].total_cost == pytest.approx(0.5)

    assert by_hash[sales].requests == 1
    assert by_hash[sales].total_cost == pytest.approx(0.05)

    # Rows written before the column existed surface as an unattributed bucket.
    assert by_hash[None].requests == 1

    # Tenant isolation: the other tenant's expensive call must not appear.
    assert sum(row.total_cost for row in breakdown) == pytest.approx(0.56)
