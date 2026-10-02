import csv
import os
import time

import httpx

from app.scripts.test_dataset import TEST_DATASET

API_URL = os.getenv(
    "ECHO_GATE_URL",
    "http://localhost:8000/api/v1/chat/completions",
)

TESTS = []

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
                    "model": "gemini-3.1-flash-lite",
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
