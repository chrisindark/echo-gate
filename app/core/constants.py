from app.modules.qdrant.qdrant_schema import CacheScope

# Versioning Tags
EMBEDDING_VERSION = "v1"
CACHE_KEY_VERSION = "v1"
PROMPT_VERSION = "1.0"

# String Prefixes & Identifiers
EMBEDDING_QUERY_PREFIX = "search_query: "
EMBEDDING_DOCUMENT_PREFIX = "search_document: "
CALLER_SERVICE_NAME = "echo-gate-api"

# Magic Numbers & Mappings
CACHE_NEGATIVE_TTL_OFFSET = -1000

SCOPE_HIERARCHY = {
    CacheScope.GLOBAL.value: 4,
    CacheScope.TENANT.value: 3,
    CacheScope.USER.value: 2,
    CacheScope.SESSION.value: 1,
    CacheScope.CONVERSATION.value: 0,
}
