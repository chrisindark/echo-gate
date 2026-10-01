from enum import Enum
from pydantic import BaseModel, Field

class IntentEnum(str, Enum):
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
        le=1.0
    )
