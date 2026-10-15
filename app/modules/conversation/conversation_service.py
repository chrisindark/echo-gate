from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.modules.conversation.conversation_model import Conversation, Message
from app.modules.conversation.conversation_schema import (
    ConversationCreate,
    MessageCreate,
)


class ConversationService:
    def __init__(self, db: Session):
        self.db = db

    def create_conversation(
        self, user_id: str, data: ConversationCreate
    ) -> Conversation:
        db_conv = Conversation(user_id=user_id, title=data.title)
        self.db.add(db_conv)
        self.db.commit()
        self.db.refresh(db_conv)
        return db_conv

    def get_user_conversations(
        self, user_id: str, skip: int = 0, limit: int = 100
    ) -> list[Conversation]:
        return (
            self.db.query(Conversation)
            .filter(Conversation.user_id == user_id)
            .order_by(desc(Conversation.created_at))
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_conversation(self, conversation_id: str, user_id: str) -> Conversation:
        # Check user_id to ensure the user owns the conversation
        return (
            self.db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .first()
        )

    def add_message(self, conversation_id: str, message: MessageCreate) -> Message:
        db_message = Message(
            conversation_id=conversation_id, role=message.role, content=message.content
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
