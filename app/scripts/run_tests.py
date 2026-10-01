import csv
import os
import time

import httpx

from app.scripts.test_dataset import TEST_DATASET

API_URL = os.getenv(
    "ECHO_GATE_URL",
    "http://localhost:8000/api/v1/chat/completions",
)

TESTS = [
    {
        "test_id": "gha_merge_free_plan_paraphrase",
        "prompt": "I have a repo with 2 apps, an api and a web now i have added .github/workflow yaml files for basic build check api service is nestjs and web is nextjs service can i have github actions running on master after every merge request ? i am on a free tier",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,  # First query for this canonical id
    },
    {
        "test_id": "gha_merge_free_plan_paraphrase_2",
        "prompt": "I have a repo with 2 apps, an api and a web now i have added .github/workflow yaml files for basic build check api service is nestjs and web is nextjs service can i have github actions running on master after every merge request ? i am not on a paid account",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_merge_free_plan_paraphrase_3",
        "prompt": "I have a mono repo with a nestjs api and nextjs web. I added .github/workflow yaml files for build checks. Can I run github actions on master after merging PRs? My account is on the free tier.",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "nestjs_redis_cache_1",
        "prompt": "How do I implement Redis caching in a NestJS application?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": False,
    },
    {
        "test_id": "nestjs_redis_cache_2",
        "prompt": "Can you show me how to add Redis caching to my NestJS api?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": True,
    },
    {
        "test_id": "capital_france_1",
        "prompt": "What is the capital of France?",
        "canonical_id": "capital_france",
        "expected_match": False,
    },
    {
        "test_id": "capital_france_2",
        "prompt": "Tell me the capital city of France.",
        "canonical_id": "capital_france",
        "expected_match": True,
    },
    {
        "test_id": "python_reverse_string_1",
        "prompt": "Write a python script to reverse a string.",
        "canonical_id": "python_reverse_string",
        "expected_match": False,
    },
    {
        "test_id": "python_reverse_string_2",
        "prompt": "How do I reverse a string in Python?",
        "canonical_id": "python_reverse_string",
        "expected_match": True,
    },
    {
        "test_id": "python_reverse_string_3",
        "prompt": "Give me Python code for string reversal.",
        "canonical_id": "python_reverse_string",
        "expected_match": True,
    },
]

TESTS.extend(TEST_DATASET)

API_KEY = "development"
TENANT_ID = "74140c21-bf4b-4dec-84ac-98c1b573f764"


def main():
    csv_file = "run_test_results.csv"
    fieldnames = [
        "test_id",
        "canonical_id",
        "query",
        "expected_match",
        "actual_match",
        "top_1_score",
        "top_1_rerank_score",
        "top_1_prompt",
        "top_2_score",
        "top_2_rerank_score",
        "top_2_prompt",
        "top_3_score",
        "top_3_rerank_score",
        "top_3_prompt",
        "accepted",
    ]

    print(f"Running {len(TESTS)} tests...")

    with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        with httpx.Client(timeout=120.0) as client:
            for test in TESTS:
                payload = {
                    "model": "gemini-3.5-flash-lite",
                    "service_name": "google-genai",
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a general purpose LLM answering questions for user queries",
                        },
                        {"role": "user", "content": test["prompt"]},
                    ],
                    "temperature": 0,
                }

                print(f"Testing: [{test['canonical_id']}] -> {test['prompt'][:50]}...")
                try:
                    response = client.post(
                        API_URL,
                        json=payload,
                        headers={"x-api-key": API_KEY, "x-tenant-id": TENANT_ID},
                    )
                except httpx.HTTPError as error:
                    print(f"Request failed: {error}")
                    time.sleep(4.2)
                    continue

                if response.status_code != 200:
                    print(f"Error: HTTP {response.status_code} - {response.text}")
                    time.sleep(4.2)
                    continue

                data = response.json()
                cache_info = data.get("cache_info", {})

                actual_match = cache_info.get("cache_hit", False)

                writer.writerow(
                    {
                        "test_id": test.get("test_id", ""),
                        "canonical_id": test["canonical_id"],
                        "query": test["prompt"],
                        "expected_match": test["expected_match"],
                        "actual_match": actual_match,
                        "top_1_score": cache_info.get("top_1_score", ""),
                        "top_1_rerank_score": cache_info.get("top_1_rerank_score", ""),
                        "top_1_prompt": cache_info.get("top_1_prompt", ""),
                        "top_2_score": cache_info.get("top_2_score", ""),
                        "top_2_rerank_score": cache_info.get("top_2_rerank_score", ""),
                        "top_2_prompt": cache_info.get("top_2_prompt", ""),
                        "top_3_score": cache_info.get("top_3_score", ""),
                        "top_3_rerank_score": cache_info.get("top_3_rerank_score", ""),
                        "top_3_prompt": cache_info.get("top_3_prompt", ""),
                        "accepted": cache_info.get("accepted", False),
                    }
                )
                print(
                    f"  -> Expected: {test['expected_match']} | Actual: {actual_match}"
                )
                time.sleep(4.2)

    print(f"\nResults written to {csv_file}")


if __name__ == "__main__":
    main()
