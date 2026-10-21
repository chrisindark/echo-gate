"""Conversation feature and chat_in_conversation integration tests.

Tests the four core guarantees:
1. Identity and thread scope: tenant_id, user_id from request.state, and
   conversation_id are copied to ChatCompletionRequest before the LLM call.
2. Auth & Quota guards: api_key_auth_optional_guard and llm_quota_guard run,
   and api_key=state.api_key is passed through to generate_completion.
3. Per-user ownership: anonymous callers bucket on a stable caller fingerprint,
   authenticated callers resolve to their user, and other callers receive 404.
4. Atomic turn: provider call first, user+assistant messages persisted together
   in one transaction, conversation.updated_at touched, and content=None guarded.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.dependencies import get_llm_quota_service, get_llm_router_service
from app.core.middlewares import TenantContextMiddleware, UserMiddleware
from app.modules.api_keys.api_keys_service import ApiKeyIdentity, api_key_service
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


def _create_test_app():
    """Create a test FastAPI app with SQLite in-memory database and conversation routes."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    captured = {
        "calls": [],
        "quota_calls": [],
    }

    class StubRouter:
        def __init__(self):
            self.return_response = None
            self.raise_exception = None

        async def generate_completion(self, request, api_key=None):
            call_info = {
                "request": request,
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "user": request.user,
                "conversation_id": request.conversation_id,
                "messages": [(m.role, m.content) for m in request.messages],
                "api_key": api_key,
                "service_name": request.service_name,
                "model": request.model,
            }
            captured["calls"].append(call_info)

            if self.raise_exception:
                raise self.raise_exception

            if self.return_response is not None:
                return self.return_response

            return ChatCompletionResponse(
                id="chatcmpl-test",
                created=1234567890,
                model=request.model,
                choices=[
                    Choice(
                        index=0,
                        message=ChatMessage(
                            role="assistant", content="Assistant reply"
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

    class StubQuota:
        async def check_quota(self, **kwargs):
            captured["quota_calls"].append(kwargs)
            return True

    stub_router = StubRouter()
    stub_quota = StubQuota()

    app = FastAPI()
    app.add_middleware(UserMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_llm_router_service] = lambda: stub_router
    app.dependency_overrides[get_llm_quota_service] = lambda: stub_quota
    app.include_router(conversation_router)

    return app, captured, stub_router, TestingSessionLocal


# --- 1. Scope & Identity copying ---------------------------------------------


def test_chat_in_conversation_copies_identity_and_scope():
    """chat_in_conversation copies tenant_id, user_id, and conversation_id before LLM call."""
    app, captured, _, _ = _create_test_app()

    # Stub API key resolution
    real_resolve = api_key_service.resolve_api_key
    api_key_service.resolve_api_key = lambda key: (
        ApiKeyIdentity(api_key_id=1, tenant_id="acme_corp", user_id="alice_123")
        if key == "sk-valid-key"
        else None
    )

    try:
        with TestClient(app) as client:
            headers = {"X-API-Key": "sk-valid-key"}

            # Create conversation
            res = client.post(
                "/api/v1/conversations", json={"title": "Test Chat"}, headers=headers
            )
            assert res.status_code == 200, res.text
            conv_id = res.json()["response"]["id"]

            # Chat in conversation
            chat_res = client.post(
                f"/api/v1/conversations/{conv_id}/chat",
                json={
                    "service_name": "ollama",
                    "model": "qwen3:8b",
                    "message": "Hello from Alice",
                },
                headers=headers,
            )
            assert chat_res.status_code == 200, chat_res.text

            assert len(captured["calls"]) == 1
            last_call = captured["calls"][-1]
            assert last_call["tenant_id"] == "acme_corp"
            assert last_call["user_id"] == "alice_123"
            assert last_call["user"] == "alice_123"
            assert last_call["conversation_id"] == conv_id
            assert last_call["api_key"] == "sk-valid-key"
            assert last_call["messages"] == [("user", "Hello from Alice")]
    finally:
        api_key_service.resolve_api_key = real_resolve


# --- 2. Auth & Quota guards --------------------------------------------------


def test_chat_in_conversation_rejects_invalid_api_key():
    """An invalid X-API-Key is rejected with 401 Unauthorized."""
    app, _, _, _ = _create_test_app()

    real_resolve = api_key_service.resolve_api_key
    api_key_service.resolve_api_key = lambda key: None

    try:
        with TestClient(app) as client:
            res = client.post(
                "/api/v1/conversations/non-existent/chat",
                json={
                    "service_name": "ollama",
                    "model": "qwen3:8b",
                    "message": "test",
                },
                headers={"X-API-Key": "bad-key"},
            )
            assert res.status_code == 401
    finally:
        api_key_service.resolve_api_key = real_resolve


def test_chat_in_conversation_executes_quota_guard():
    """llm_quota_guard runs and receives caller bucket, tenant, provider, and model."""
    app, captured, _, _ = _create_test_app()

    with TestClient(app) as client:
        # Create anonymous conversation
        headers = {"X-Session-ID": "sess-quota-1"}
        res = client.post(
            "/api/v1/conversations", json={"title": "Quota Test"}, headers=headers
        )
        conv_id = res.json()["response"]["id"]

        chat_res = client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "ollama",
                "model": "qwen3:8b",
                "message": "Quota check",
            },
            headers=headers,
        )
        assert chat_res.status_code == 200, chat_res.text
        assert len(captured["quota_calls"]) >= 1
        quota_call = captured["quota_calls"][-1]
        assert quota_call["provider"] == "ollama"
        assert quota_call["model"] == "qwen3:8b"
        assert quota_call["tenant_id"] == "anonymous"
        assert quota_call["bucket_id"].startswith("anon:")


# --- 3. Per-user ownership and user resolution -------------------------------


def test_anonymous_user_isolation_by_session_and_404_for_other():
    """Anonymous callers bucket on client identity; one cannot access another's conversation."""
    app, _, _, _ = _create_test_app()

    with TestClient(app) as client:
        # Caller A
        res_a = client.post(
            "/api/v1/conversations",
            json={"title": "User A Chat"},
            headers={"X-Session-ID": "session-A"},
        )
        assert res_a.status_code == 200
        conv_a_id = res_a.json()["response"]["id"]

        # Caller B tries to access Conversation A -> 404
        get_b = client.get(
            f"/api/v1/conversations/{conv_a_id}",
            headers={"X-Session-ID": "session-B"},
        )
        assert get_b.status_code == 404

        # Caller B tries to chat in Conversation A -> 404
        chat_b = client.post(
            f"/api/v1/conversations/{conv_a_id}/chat",
            json={"model": "qwen3:8b", "message": "intrusion"},
            headers={"X-Session-ID": "session-B"},
        )
        assert chat_b.status_code == 404

        # Caller A can access Conversation A
        get_a = client.get(
            f"/api/v1/conversations/{conv_a_id}",
            headers={"X-Session-ID": "session-A"},
        )
        assert get_a.status_code == 200


def test_authenticated_cross_tenant_isolation():
    """Tenant A's user cannot access Tenant B's conversation (returns 404)."""
    app, _, _, _ = _create_test_app()

    def mock_resolve(key):
        if key == "key-tenant-a":
            return ApiKeyIdentity(api_key_id=1, tenant_id="tenant_a", user_id="user_1")
        if key == "key-tenant-b":
            return ApiKeyIdentity(api_key_id=2, tenant_id="tenant_b", user_id="user_1")
        return None

    real_resolve = api_key_service.resolve_api_key
    api_key_service.resolve_api_key = mock_resolve

    try:
        with TestClient(app) as client:
            # Create conv in Tenant A
            res = client.post(
                "/api/v1/conversations",
                json={"title": "Tenant A Chat"},
                headers={"X-API-Key": "key-tenant-a"},
            )
            conv_id = res.json()["response"]["id"]

            # Tenant B with same user_id tries to chat in Tenant A's conversation
            chat_res = client.post(
                f"/api/v1/conversations/{conv_id}/chat",
                json={"model": "qwen3:8b", "message": "Cross tenant probe"},
                headers={"X-API-Key": "key-tenant-b"},
            )
            assert chat_res.status_code == 404
    finally:
        api_key_service.resolve_api_key = real_resolve


# --- 4. Atomic turn: provider first, single transaction, touch updated_at, guard content=None


def test_atomic_turn_successful_turn_updates_updated_at():
    """Provider call happens first, both user and assistant messages are saved, updated_at touched."""
    app, _, _, SessionLocal = _create_test_app()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/conversations",
            json={"title": "Atomic Turn Test"},
            headers={"X-Session-ID": "sess-atomic-1"},
        )
        conv_id = res.json()["response"]["id"]

        # Backdate the conversation slightly to confirm updated_at changes
        with SessionLocal() as session:
            conv = session.query(Conversation).filter_by(id=conv_id).first()
            conv.updated_at = datetime.now(UTC) - timedelta(minutes=5)
            session.commit()
            old_updated_at = conv.updated_at

        # Execute chat
        chat_res = client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "ollama",
                "model": "qwen3:8b",
                "message": "What is 2+2?",
            },
            headers={"X-Session-ID": "sess-atomic-1"},
        )
        assert chat_res.status_code == 200, chat_res.text

        # Verify DB state
        with SessionLocal() as session:
            conv = session.query(Conversation).filter_by(id=conv_id).first()
            assert conv.updated_at > old_updated_at

            messages = (
                session.query(Message)
                .filter_by(conversation_id=conv_id)
                .order_by(Message.created_at)
                .all()
            )
            assert len(messages) == 2
            assert messages[0].role == "user"
            assert messages[0].content == "What is 2+2?"
            assert messages[1].role == "assistant"
            assert messages[1].content == "Assistant reply"


def test_atomic_turn_rolls_back_if_provider_call_fails():
    """If the LLM call fails, no user message is saved to the database."""
    app, _, stub_router, SessionLocal = _create_test_app()
    stub_router.raise_exception = RuntimeError("Provider down")

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/conversations",
            json={"title": "Failure Test"},
            headers={"X-Session-ID": "sess-fail"},
        )
        conv_id = res.json()["response"]["id"]

        # Attempt chat which will fail in provider
        with pytest.raises(RuntimeError, match="Provider down"):
            client.post(
                f"/api/v1/conversations/{conv_id}/chat",
                json={
                    "service_name": "ollama",
                    "model": "qwen3:8b",
                    "message": "This will fail",
                },
                headers={"X-Session-ID": "sess-fail"},
            )

        # Confirm nothing was written to messages
        with SessionLocal() as session:
            messages = session.query(Message).filter_by(conversation_id=conv_id).all()
            assert len(messages) == 0


def test_guard_content_none_does_not_persist_or_crash():
    """If provider returns content=None, turn is not persisted and request does not crash."""
    app, _, stub_router, SessionLocal = _create_test_app()
    stub_router.return_response = ChatCompletionResponse(
        id="chatcmpl-none",
        created=1234567890,
        model="qwen3:8b",
        choices=[
            Choice(
                index=0,
                message=ChatMessage(role="assistant", content=None),
                finish_reason="stop",
            )
        ],
    )

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/conversations",
            json={"title": "Null Content Test"},
            headers={"X-Session-ID": "sess-null"},
        )
        conv_id = res.json()["response"]["id"]

        chat_res = client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={
                "service_name": "ollama",
                "model": "qwen3:8b",
                "message": "Prompt with null response",
            },
            headers={"X-Session-ID": "sess-null"},
        )
        assert chat_res.status_code == 200
        data = chat_res.json()["response"]
        assert data["choices"][0]["message"]["content"] is None

        # Verify no messages were persisted
        with SessionLocal() as session:
            messages = session.query(Message).filter_by(conversation_id=conv_id).all()
            assert len(messages) == 0


def test_multi_turn_history_accumulates():
    """Subsequent chat calls send full conversation history to the LLM."""
    app, captured, _, _ = _create_test_app()

    with TestClient(app) as client:
        headers = {"X-Session-ID": "sess-history"}
        res = client.post(
            "/api/v1/conversations", json={"title": "Multi Turn"}, headers=headers
        )
        conv_id = res.json()["response"]["id"]

        # Turn 1
        client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={"model": "qwen3:8b", "message": "Turn 1 user"},
            headers=headers,
        )
        assert captured["calls"][-1]["messages"] == [("user", "Turn 1 user")]

        # Turn 2
        client.post(
            f"/api/v1/conversations/{conv_id}/chat",
            json={"model": "qwen3:8b", "message": "Turn 2 user"},
            headers=headers,
        )
        assert captured["calls"][-1]["messages"] == [
            ("user", "Turn 1 user"),
            ("assistant", "Assistant reply"),
            ("user", "Turn 2 user"),
        ]

        # Get conversation
        get_res = client.get(f"/api/v1/conversations/{conv_id}", headers=headers)
        assert get_res.status_code == 200
        messages = get_res.json()["response"]["messages"]
        assert len(messages) == 4
        assert [m["content"] for m in messages] == [
            "Turn 1 user",
            "Assistant reply",
            "Turn 2 user",
            "Assistant reply",
        ]
