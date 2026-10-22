import hashlib
import logging
import re

from app.modules.qdrant.qdrant_schema import CacheScope

logger = logging.getLogger(__name__)


class EntityExtractorService:
    """
    Extracts entities from user prompts using fast heuristics and regex.
    For a high-throughput LLM gateway, regex provides sub-millisecond extraction
    compared to heavy NLP models (like spaCy or GLiNER), while covering the most
    critical cache-busting entities (URLs, Versions, Currencies, Dates, IDs).
    """

    def __init__(self):
        # Pre-compile regex patterns for performance
        self.patterns = {
            "URL": re.compile(r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*"),
            "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            "NUMBER": re.compile(r"\b\d+(?:[.,]\d+)*%?"),
            "UUID": re.compile(
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
            ),
            "VERSION": re.compile(
                r"\b(?:v|version\s)?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?\b",
                re.IGNORECASE,
            ),
            "CURRENCY": re.compile(r"[\$\£\€\¥]\s?\d+(?:,\d{3})*(?:\.\d{2})?"),
            "DATE_ISO": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
            "DATE_COMMON": re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
            "ACCOUNT_ID": re.compile(
                r"\b(?:acc|account|id)[\s_:-]*(?=[A-Z0-9]*\d)[A-Z0-9]{8,15}\b",
                re.IGNORECASE,
            ),
            "IPV4": re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"),
            "IPV6": re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}\b"),
            "MAC_ADDRESS": re.compile(
                r"\b(?:[0-9A-Fa-f]{2}[:-]){5}(?:[0-9A-Fa-f]{2})\b"
            ),
            "API_KEY": re.compile(
                r"\b(?:api_key|apikey|token|secret)[\s=:]+['\"]?[A-Za-z0-9_\-]{16,}['\"]?\b",
                re.IGNORECASE,
            ),
        }

        # Extracted entities mapped to whether they enforce a strict scope downgrade
        # (e.g. finding an email or account ID implies the cache shouldn't be GLOBAL)
        self.sensitive_entity_types = {"EMAIL", "ACCOUNT_ID", "UUID", "API_KEY"}

    @staticmethod
    def _clean_url(url: str) -> str:
        """
        Strips trailing punctuation, quotes, delimiters, and unbalanced brackets/parentheses.
        """
        trailing_punct = ".,;:!?<>\"'}"
        url = url.rstrip(trailing_punct)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(trailing_punct)
        while url.endswith("]") and url.count("]") > url.count("["):
            url = url[:-1].rstrip(trailing_punct)
        return url

    def extract_entities(self, text: str) -> dict[str, list[str]]:
        """
        Extracts entities from text, categorized by type.
        """
        extracted = {}
        for entity_type, pattern in self.patterns.items():
            matches = pattern.findall(text)
            if matches:
                if entity_type == "URL":
                    cleaned_urls = [self._clean_url(m) for m in matches]
                    matches = [u for u in cleaned_urls if u]
                if matches:
                    # Deduplicate while preserving order
                    extracted[entity_type] = list(dict.fromkeys(matches))

        return extracted

    def get_tags_and_max_scope(self, text: str) -> tuple[list[str], CacheScope]:
        """
        Extracts entity tags and determines the maximum allowable cache scope in a single pass.
        Sensitive entities (e.g. EMAIL, API_KEY, ACCOUNT_ID, UUID) are hashed with SHA-256
        to avoid storing raw secrets and PII in Qdrant payloads, and downgrade max scope to USER.
        Tags are sorted for determinism.
        """
        tags: list[str] = []
        sensitive_types_found: set[str] = set()

        entities_dict = self.extract_entities(text)

        for entity_type, values in entities_dict.items():
            is_sensitive = entity_type in self.sensitive_entity_types
            if is_sensitive and values:
                sensitive_types_found.add(entity_type)

            for val in values:
                clean_val = val.strip().lower()
                if is_sensitive:
                    hashed_val = hashlib.sha256(clean_val.encode()).hexdigest()
                    tags.append(f"{entity_type}:{hashed_val}")
                else:
                    tags.append(f"{entity_type}:{clean_val}")

        tags.sort()

        if sensitive_types_found:
            types_str = ", ".join(sorted(sensitive_types_found))
            logger.info(
                f"Sensitive entity ({types_str}) detected. Downgrading max scope to USER."
            )
            return tags, CacheScope.USER

        return tags, CacheScope.GLOBAL

    extract_tags_and_scope = get_tags_and_max_scope
    extract_tags_and_max_scope = get_tags_and_max_scope
    get_tags_and_scope = get_tags_and_max_scope

    def get_qdrant_entity_tags(self, text: str) -> list[str]:
        """
        Extracts entities and formats them as flat string tags for Qdrant payload.
        Format: "TYPE:value" (e.g., "VERSION:3.11", "URL:https://google.com").
        Sensitive entities (e.g. EMAIL, API_KEY, ACCOUNT_ID, UUID) are hashed with SHA-256
        to avoid storing raw secrets and PII in Qdrant payloads.
        Returns sorted tags for determinism.
        """
        tags, _ = self.get_tags_and_max_scope(text)
        return tags

    def determine_max_scope(self, text: str) -> CacheScope:
        """
        Determines the maximum allowable cache scope based on extracted entities.
        If sensitive account information is detected, restricts from GLOBAL to USER.
        """
        _, scope = self.get_tags_and_max_scope(text)
        return scope
