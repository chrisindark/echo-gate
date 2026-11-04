"""Conversation turns served from cache.

A cache hit changes how the answer was produced, not whether the turn is part of
the conversation. These tests pin that: a cached reply is still persisted as a
user+assistant pair, is still visible to the next completion call, and is still
returned with its ``cache_info`` provenance intact.
"""

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.dependencies import get_llm_quota_service, get_llm_router_service
from app.core.middlewares import TenantContextMiddleware, UserMiddleware
from app.modules.chat.chat_schema import (
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    Usage,
)
from app.modules.conversation.conversation_controller import (
    api_v1_router as conversation_router,
)
from app.modules.conversation.conversation_model import Conversation, Message

CACHED_ANSWER = "Cached: the answer to your earlier question is 42."


def _cached_response(model: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-cached",
        created=1_700_000_000,
        model=model,
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=CACHED_ANSWER),
                finish_reason="stop",
            )
        ],
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        cache_info={
            "source": "qdrant_semantic",
            "score": 0.97,
            "routing_decision": "QDRANT_SEMANTIC",
        },
    )


def _build_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    calls: list[dict] = []

    class StubRouter:
        async def generate_completion(
            self, request, api_key=None, timeout_budget_ms=None
        ):
            calls.append(
                {
                    "messages": [(m.role, m.content) for m in request.messages],
                    "conversation_id": request.conversation_id,
                    "tenant_id": request.tenant_id,
                }
            )
            return _cached_response(request.model)

    class StubQuota:
        async def check_quota(self, **kwargs):
            return True

    app = FastAPI()
    app.add_middleware(UserMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_router_service] = StubRouter
    app.dependency_overrides[get_llm_quota_service] = StubQuota
    app.include_router(conversation_router)
    return app, calls, SessionLocal


def test_cached_reply_is_persisted_as_a_turn():
    """A cache hit must still record both messages of the turn."""
    app, calls, SessionLocal = _build_app()

    with TestClient(app) as client:
        conv_id = client.post("/api/v1/conversations", json={"title": "Cached"}).json()[
            "response"
        ]["id"]

        res = client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "message": "What did we decide?",
            },
        )

        assert res.status_code == 200, res.text
        body = res.json()["response"]
        assert body["choices"][0]["message"]["content"] == CACHED_ANSWER
        assert body["cache_info"]["routing_decision"] == "QDRANT_SEMANTIC"

    with SessionLocal() as db:
        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id)
            # Order the way the production read path does. Ordering by `id`
            # (a UUID) would be random, and would therefore hide — not test —
            # the pair ordering that `record_turn` guarantees.
            .order_by(Message.created_at)
            .all()
        )
        assert [(m.role, m.content) for m in messages] == [
            ("user", "What did we decide?"),
            ("assistant", CACHED_ANSWER),
        ]

    assert len(calls) == 1
    assert calls[0]["conversation_id"] == conv_id


def test_cached_reply_does_not_duplicate_the_user_message():
    """The user turn is persisted once, not once per cache layer that answered."""
    app, _, SessionLocal = _build_app()

    with TestClient(app) as client:
        conv_id = client.post("/api/v1/conversations", json={"title": "Once"}).json()[
            "response"
        ]["id"]
        client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={"service_name": "openai", "model": "gpt-4o-mini", "message": "hello"},
        )

    with SessionLocal() as db:
        user_messages = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id, Message.role == "user")
            .all()
        )
        assert len(user_messages) == 1


def test_cached_reply_enters_the_next_turn_history():
    """A cached assistant reply becomes context for the following request."""
    app, calls, _ = _build_app()

    with TestClient(app) as client:
        conv_id = client.post(
            "/api/v1/conversations", json={"title": "History"}
        ).json()["response"]["id"]
        client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "message": "first question",
            },
        )
        second = client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "openai",
                "model": "gpt-4o-mini",
                "message": "second question",
            },
        )
        assert second.status_code == 200, second.text

    assert len(calls) == 2
    assert calls[1]["messages"] == [
        ("user", "first question"),
        ("assistant", CACHED_ANSWER),
        ("user", "second question"),
    ]


def test_cached_reply_touches_conversation_updated_at():
    """Serving from cache is still activity, so ``updated_at`` must advance."""
    app, _, SessionLocal = _build_app()
    planted = datetime(2020, 1, 1, 0, 0, 0)

    with TestClient(app) as client:
        conv_id = client.post("/api/v1/conversations", json={"title": "Touch"}).json()[
            "response"
        ]["id"]

        with SessionLocal() as db:
            # Plant an old timestamp so a no-op update cannot pass by accident.
            row = db.query(Conversation).filter(Conversation.id == conv_id).one()
            row.updated_at = planted
            db.commit()

        client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={"service_name": "openai", "model": "gpt-4o-mini", "message": "ping"},
        )

    with SessionLocal() as db:
        after = (
            db.query(Conversation).filter(Conversation.id == conv_id).one().updated_at
        )
    assert after is not None
    assert after > planted
