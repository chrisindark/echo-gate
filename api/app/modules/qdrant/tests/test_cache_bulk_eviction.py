"""Cache inspector primitives in the Qdrant cache collection.

Covers ``search_by_exact_hash`` (an inspector lookup: no cacheability or expiry
filter) and ``bulk_evict`` (filtered, bounded eviction that reports the exact
matches and the Redis keys a caller still has to purge).

Qdrant is replaced by an in-memory fake that applies the filters the service
builds, so the assertions are about surviving points and reported counts rather
than about filter object shapes.
"""

import time
import unittest
from types import SimpleNamespace

from qdrant_client.http import models

from app.modules.qdrant.llm_cache_collection_service import (
    LlmCacheCollectionService,
)

DAY = 24 * 60 * 60
NOW = 1_800_000_000


class FakeQdrantClientService:
    """In-memory stand-in that applies match and range conditions."""

    def __init__(self, points: dict[str, dict] | None = None) -> None:
        self.points: dict[str, dict] = dict(points or {})
        self.deleted_filters: list[list[str]] = []
        self.scroll_payload_fields: list[object] = []

    # Collection lifecycle (construction path).
    def collection_exists(self, collection_name: str) -> bool:
        return False

    def create_collection(self, **kwargs) -> bool:
        return True

    def count(self, collection_name, count_filter=None, exact=True):
        return sum(
            1 for payload in self.points.values() if _matches(payload, count_filter)
        )

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=10,
        with_payload=True,
        with_vectors=False,
        **kwargs,
    ):
        self.scroll_payload_fields.append(with_payload)
        matched = [
            (point_id, payload)
            for point_id, payload in self.points.items()
            if _matches(payload, scroll_filter)
        ][:limit]

        def _selected(payload: dict) -> dict:
            if isinstance(with_payload, list):
                return {key: payload[key] for key in with_payload if key in payload}
            return payload

        return [
            SimpleNamespace(id=point_id, payload=_selected(payload))
            for point_id, payload in matched
        ], None

    def delete(self, collection_name, points_selector, wait=False):
        ids = [
            point_id
            for point_id, payload in self.points.items()
            if _matches(payload, points_selector)
        ]
        for point_id in ids:
            self.points.pop(point_id, None)
        self.deleted_filters.append(ids)


def _matches(payload: dict, points_filter) -> bool:
    if points_filter is None:
        return True
    for condition in points_filter.must or []:
        if not _condition_matches(payload, condition):
            return False
    return True


def _condition_matches(payload: dict, condition) -> bool:
    if not isinstance(condition, models.FieldCondition):
        return True

    value = payload.get(condition.key)
    if condition.range is not None:
        if value is None:
            return False
        if condition.range.lt is not None and not value < condition.range.lt:
            return False
        if condition.range.gte is not None and not value >= condition.range.gte:
            return False
        return True
    if condition.match is not None:
        return str(value) == str(condition.match.value)
    return True


def _entry(
    exact_hash: str,
    **overrides,
) -> dict:
    payload = {
        "prompt": f"prompt-{exact_hash}",
        "user_prompt": f"user-{exact_hash}",
        "response": {"choices": []},
        "exact_hash": exact_hash,
        "model": "gpt-4o-mini",
        "intent": "general_query",
        "scope": "TENANT",
        "tenant_id": "acme",
        "cacheable": True,
        "created_at": NOW - 10 * DAY,
        "expires_at": NOW + 10 * DAY,
    }
    payload.update(overrides)
    return payload


class InspectorLookupTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQdrantClientService(
            {
                "live": _entry("hash-live"),
                "expired": _entry(
                    "hash-expired", expires_at=NOW - DAY, cacheable=False
                ),
                "other-tenant": _entry("hash-other", tenant_id="globex"),
            }
        )
        self.service = LlmCacheCollectionService(client_service=self.fake)

    def test_exact_lookup_ignores_cacheability_and_expiry(self):
        hits = self.service.search_by_exact_hash("hash-expired")

        self.assertEqual([hit["id"] for hit in hits], ["expired"])
        self.assertIsNone(hits[0]["score"])
        self.assertEqual(hits[0]["payload"]["exact_hash"], "hash-expired")

    def test_exact_lookup_applies_extra_filters(self):
        self.assertEqual(
            self.service.search_by_exact_hash(
                "hash-other", filter_payload={"tenant_id": "acme"}
            ),
            [],
        )
        hits = self.service.search_by_exact_hash(
            "hash-other", filter_payload={"tenant_id": "globex"}
        )
        self.assertEqual([hit["id"] for hit in hits], ["other-tenant"])

    def test_exact_lookup_respects_the_limit(self):
        self.fake.points["dup"] = _entry("hash-live")
        hits = self.service.search_by_exact_hash("hash-live", limit=1)
        self.assertEqual(len(hits), 1)


class BulkEvictionTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeQdrantClientService(
            {
                "keep-1": _entry("hash-keep-1"),
                "keep-2": _entry("hash-keep-2", intent="code_generation"),
                "old": _entry("hash-old", created_at=NOW - 40 * DAY),
                "expired": _entry("hash-expired", expires_at=NOW - DAY),
                "other": _entry("hash-other", model="gemini-3.1-flash-lite"),
            }
        )
        self.service = LlmCacheCollectionService(client_service=self.fake)

    def test_dry_run_reports_matches_and_deletes_nothing(self):
        plan = self.service.bulk_evict(intent="general_query", now=NOW)

        self.assertEqual(plan["matched"], 4)
        self.assertEqual(plan["deleted"], 0)
        self.assertEqual(plan["point_ids"], [])
        self.assertEqual(plan["exact_hashes"], [])
        self.assertEqual(self.fake.deleted_filters, [])
        self.assertEqual(len(self.fake.points), 5)

    def test_intent_sweep_removes_only_that_intent_and_reports_hashes(self):
        plan = self.service.bulk_evict(intent="code_generation", dry_run=False, now=NOW)

        self.assertEqual(plan["matched"], 1)
        self.assertEqual(plan["deleted"], 1)
        self.assertEqual(plan["point_ids"], ["keep-2"])
        self.assertEqual(plan["exact_hashes"], ["hash-keep-2"])
        self.assertEqual(set(self.fake.points), {"keep-1", "old", "expired", "other"})

    def test_age_sweep_uses_created_at(self):
        plan = self.service.bulk_evict(older_than_days=30, dry_run=False, now=NOW)

        self.assertEqual(plan["point_ids"], ["old"])
        self.assertEqual(
            set(self.fake.points), {"keep-1", "keep-2", "expired", "other"}
        )

    def test_expired_only_sweep_uses_expires_at(self):
        plan = self.service.bulk_evict(expired_only=True, dry_run=False, now=NOW)

        self.assertEqual(plan["point_ids"], ["expired"])

    def test_filters_are_combined(self):
        plan = self.service.bulk_evict(
            model="gpt-4o-mini", older_than_days=30, dry_run=False, now=NOW
        )

        self.assertEqual(plan["point_ids"], ["old"])

    def test_scroll_only_requests_the_exact_hash_payload(self):
        self.service.bulk_evict(model="gpt-4o-mini", dry_run=False, now=NOW)
        self.assertIn(["exact_hash"], self.fake.scroll_payload_fields)

    def test_no_matches_is_a_clean_noop(self):
        plan = self.service.bulk_evict(intent="does_not_exist", dry_run=False, now=NOW)

        self.assertEqual(
            plan,
            {
                "matched": 0,
                "deleted": 0,
                "point_ids": [],
                "exact_hashes": [],
            },
        )
        self.assertEqual(self.fake.deleted_filters, [])

    def test_an_empty_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.bulk_evict(dry_run=False, now=NOW)
        self.assertEqual(self.fake.deleted_filters, [])

    def test_a_sweep_above_the_limit_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.service.bulk_evict(
                model="gpt-4o-mini", limit=2, dry_run=False, now=NOW
            )

        self.assertIn("above the 2 limit", str(ctx.exception))
        self.assertEqual(self.fake.deleted_filters, [])
        self.assertEqual(len(self.fake.points), 5)

    def test_a_non_positive_age_is_rejected(self):
        with self.assertRaises(ValueError):
            self.service.bulk_evict(older_than_days=0, dry_run=False, now=NOW)

    def test_now_defaults_to_the_current_clock(self):
        self.fake.points["stale"] = _entry(
            "hash-stale", created_at=int(time.time()) - 5 * DAY
        )
        plan = self.service.bulk_evict(older_than_days=1, dry_run=True)
        self.assertEqual(plan["matched"], 1)


if __name__ == "__main__":
    unittest.main()
