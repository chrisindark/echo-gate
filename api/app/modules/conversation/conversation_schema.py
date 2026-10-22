import datetime

from pydantic import BaseModel


class MessageCreate(BaseModel):
    role: str
    content: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ConversationCreate(BaseModel):
    title: str | None = "New Conversation"


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class ConversationWithMessagesResponse(ConversationResponse):
    messages: list[MessageResponse]


class ChatCompletionRequestWithConversation(BaseModel):
    service_name: str | None = "ollama"
    model: str
    message: str
    temperature: float | None = 1.0
