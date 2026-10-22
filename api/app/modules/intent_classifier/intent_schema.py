from enum import Enum

from pydantic import BaseModel, Field


class IntentEnum(str, Enum):
    EMPTY = ""
    CODE_GENERATION = "code_generation"
    DATA_EXTRACTION = "data_extraction"
    SUMMARIZATION = "summarization"
    GENERAL_QUERY = "general_query"
    CODE_EXPLANATION = "code_explanation"
    DEBUGGING = "debugging"
    ERROR_ANALYSIS = "error_analysis"
    REFACTORING = "refactoring"
    OPTIMIZATION = "optimization"
    TEST_GENERATION = "test_generation"
    CHITCHAT = "chitchat"
    GREETING = "greeting"
    # Time-sensitive domains
    WEATHER = "weather"
    FINANCE_MARKET = "finance_market"
    PRODUCT_INFO = "product_info"
    FACTUAL_STATIC = "factual_static"


class IntentClassificationResult(BaseModel):
    intent: IntentEnum
    time_sensitivity: float = Field(
        ...,
        description="Score between 0.0 (static) and 1.0 (highly time-sensitive)",
        ge=0.0,
        le=1.0,
    )
    core_operation: str = Field(
        "",
        description="The primary action verb or operation being requested, e.g. 'book', 'cancel', 'compare', 'sort', 'delete', 'translate'. Max 2 words.",
    )
    core_subject: str = Field(
        "",
        description="The primary noun or subject being acted upon, e.g. 'flight ticket', 'docker container', 'apple stock', 'chocolate cake'. Max 4 words.",
    )
    subject_modifier: str | None = Field(
        None,
        description="The specific attribute, quality, or constraint applied to the subject, e.g. 'cheapest' for flights, 'latest' for news, 'secure' for connection. If none applies, return null.",
    )
    action_modifier: str | None = Field(
        None,
        description="The style, method, or parameters defining how the operation should be performed, e.g. 'alphabetically', 'silently', 'quickly'. If none applies, return null.",
    )
