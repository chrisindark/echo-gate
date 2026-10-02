from typing import Any

from pydantic import BaseModel, model_validator

ALLOWED_MODELS = {
    "ollama": [
        "qwen3:8b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:1.5b",
        "qwen2.5-coder:3b",
        "qwen2.5-coder:7b",
    ],
    "gemini": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ],
    "google-genai": [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ],
    "openai": [
        "gpt-4o-mini",
    ],
    "groq": ["openai/gpt-oss-20b", "openai/gpt-oss-safeguard-20b"],
    "openrouter": [
        "qwen/qwen3.8-27b:free",
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
    ],
}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    service_name: str | None = "ollama"
    model: str
    messages: list[ChatMessage]
    temperature: float = 1.0
    top_p: float | None = 1.0
    n: int | None = 1
    stream: bool | None = False
    stop: list[str] | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = 0.0
    frequency_penalty: float | None = 0.0
    logit_bias: dict[str, float] | None = None
    user: dict[str, Any] | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    response_format: dict | None = None

    @model_validator(mode="after")
    def validate_model_for_service(self) -> "ChatCompletionRequest":
        service = self.service_name or "ollama"
        if service in ALLOWED_MODELS and self.model not in ALLOWED_MODELS[service]:
            allowed = ", ".join(ALLOWED_MODELS[service])
            raise ValueError(
                f"Model '{self.model}' is not allowed for provider '{service}'. Allowed models are: {allowed}"
            )
        return self


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class Usage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: Usage | None = None
    system_fingerprint: str | None = None
    cache_info: dict[str, Any] | None = None
