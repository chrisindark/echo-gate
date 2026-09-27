from enum import Enum


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
