from datetime import UTC, datetime, timedelta

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.rls import ANONYMOUS_TENANT, get_rls_context
from app.modules.conversation.conversation_model import Conversation, Message
from app.modules.conversation.conversation_schema import (
    ConversationCreate,
    MessageCreate,
)


class ConversationService:
    def __init__(self, db: Session):
        self.db = db

    def create_conversation(
        self, user_id: str, data: ConversationCreate, tenant_id: str | None = None
    ) -> Conversation:
        t_id = tenant_id or get_rls_context().tenant_id or ANONYMOUS_TENANT
        db_conv = Conversation(tenant_id=t_id, user_id=user_id, title=data.title)
        self.db.add(db_conv)
        self.db.commit()
        self.db.refresh(db_conv)
        return db_conv

    def get_user_conversations(
        self,
        user_id: str,
        tenant_id: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Conversation]:
        query = self.db.query(Conversation).filter(Conversation.user_id == user_id)
        if tenant_id:
            query = query.filter(Conversation.tenant_id == tenant_id)
        return (
            query.order_by(desc(Conversation.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_conversation(
        self, conversation_id: str, user_id: str, tenant_id: str | None = None
    ) -> Conversation | None:
        # Check user_id and tenant_id to ensure ownership and isolation
        query = self.db.query(Conversation).filter(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        )
        if tenant_id:
            query = query.filter(Conversation.tenant_id == tenant_id)
        return query.first()

    def update_conversation(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
        tenant_id: str | None = None,
    ) -> Conversation | None:
        conv = self.get_conversation(
            conversation_id, user_id=user_id, tenant_id=tenant_id
        )
        if not conv:
            return None
        conv.title = title
        conv.updated_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(conv)
        return conv

    def delete_conversation(
        self,
        conversation_id: str,
        user_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        conv = self.get_conversation(
            conversation_id, user_id=user_id, tenant_id=tenant_id
        )
        if not conv:
            return False
        self.db.delete(conv)
        self.db.commit()
        return True

    def record_turn(
        self,
        conversation: Conversation,
        user_content: str,
        assistant_content: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[Message, Message]:
        """Atomically persist a user turn and assistant reply in one transaction,

        touching conversation.updated_at.
        """
        now = datetime.now(UTC)
        # The two rows would otherwise share one timestamp, and every read path
        # orders only by `created_at` — so the pair's order, and therefore the
        # prompt history sent on the next turn, would be undefined. Stamp the
        # reply one microsecond later so the ordering is unambiguous and stable.
        reply_at = now + timedelta(microseconds=1)
        t_id = (
            tenant_id
            or conversation.tenant_id
            or get_rls_context().tenant_id
            or ANONYMOUS_TENANT
        )
        u_id = user_id or conversation.user_id
        user_msg = Message(
            conversation_id=conversation.id,
            tenant_id=t_id,
            user_id=u_id,
            role="user",
            content=user_content,
            created_at=now,
        )
        assistant_msg = Message(
            conversation_id=conversation.id,
            tenant_id=t_id,
            user_id=u_id,
            role="assistant",
            content=assistant_content,
            created_at=reply_at,
        )
        self.db.add(user_msg)
        self.db.add(assistant_msg)
        conversation.updated_at = reply_at
        self.db.commit()
        self.db.refresh(user_msg)
        self.db.refresh(assistant_msg)
        self.db.refresh(conversation)
        return user_msg, assistant_msg

    def add_message(
        self,
        conversation_id: str,
        message: MessageCreate,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> Message:
        t_id = tenant_id or get_rls_context().tenant_id or ANONYMOUS_TENANT
        u_id = user_id or get_rls_context().user_id
        db_message = Message(
            conversation_id=conversation_id,
            tenant_id=t_id,
            user_id=u_id,
            role=message.role,
            content=message.content,
        )
        self.db.add(db_message)
        self.db.commit()
        self.db.refresh(db_message)
        return db_message

    def get_conversation_messages(self, conversation_id: str) -> list[Message]:
        return (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
            .all()
        )
