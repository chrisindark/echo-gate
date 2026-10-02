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
            "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
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
                r"\b(?:acc|account|id)[\s_:-]*[A-Z0-9]{8,15}\b", re.IGNORECASE
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

    def extract_entities(self, text: str) -> dict[str, list[str]]:
        """
        Extracts entities from text, categorized by type.
        """
        extracted = {}
        for entity_type, pattern in self.patterns.items():
            matches = pattern.findall(text)
            if matches:
                # Deduplicate while preserving order
                extracted[entity_type] = list(dict.fromkeys(matches))

        return extracted

    def get_qdrant_entity_tags(self, text: str) -> list[str]:
        """
        Extracts entities and formats them as flat string tags for Qdrant payload.
        Format: "TYPE:value" (e.g., "VERSION:3.11", "URL:https://google.com")
        """
        tags = []
        entities_dict = self.extract_entities(text)

        for entity_type, values in entities_dict.items():
            for val in values:
                # Clean up and normalize the tag
                clean_val = val.strip().lower()
                tags.append(f"{entity_type}:{clean_val}")

        return tags

    def determine_max_scope(self, text: str) -> CacheScope:
        """
        Determines the maximum allowable cache scope based on extracted entities.
        If sensitive account information is detected, restricts from GLOBAL to USER.
        """

        entities_dict = self.extract_entities(text)

        for sensitive_type in self.sensitive_entity_types:
            if entities_dict.get(sensitive_type):
                logger.info(
                    f"Sensitive entity ({sensitive_type}) detected. Downgrading max scope to USER."
                )
                return CacheScope.USER

        return CacheScope.GLOBAL
