import csv
import json
import os
import time

import httpx

API_URL = os.getenv(
    "ECHO_GATE_URL",
    "http://localhost:8000/api/v1/chat/completions",
)
RESULTS_CSV = os.getenv("ECHO_GATE_RESULTS_CSV", "send_prompt_results.csv")
API_KEY = "development"
TENANT_ID = "74140c21-bf4b-4dec-84ac-98c1b573f764"

THRESHOLD_TESTS = [
    {
        "test_id": "gha_merge_free_plan_paraphrase",
        "prompt": "Can GitHub Actions build my NestJS API and Next.js app automatically when a PR is merged into master on the free plan?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_free_account_ci_after_merge",
        "prompt": "With a free GitHub account, can a workflow run on the default branch after a pull request is merged?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_workflow_pr_open_not_merge",
        "prompt": "How do I make a workflow run when a PR opens but not after it is merged?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,
    },
    {
        "test_id": "gha_disable_default_branch_push",
        "prompt": "How can I stop GitHub Actions from running on pushes to the default branch?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,
    },
    {
        "test_id": "gha_deploy_aws_release",
        "prompt": "How do I deploy an application to AWS with GitHub Actions after creating a release?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,
    },
    {
        "test_id": "gha_cache_npm_dependencies",
        "prompt": "How can I cache npm dependencies between GitHub Actions workflow runs?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,
    },
    {
        "test_id": "nestjs_redis_cache_api_result",
        "prompt": "In NestJS, how can I use Redis to cache the result of an API request?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": True,
    },
    {
        "test_id": "nestjs_redis_backed_cache_service",
        "prompt": "What's a good way to add a Redis-backed cache to a NestJS service?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": True,
    },
    {
        "test_id": "nestjs_redis_bullmq_queue",
        "prompt": "How can I use Redis queues with BullMQ in a NestJS app?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": False,
    },
    {
        "test_id": "nestjs_redis_login_sessions",
        "prompt": "How do I store login sessions in Redis for a NestJS application?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": False,
    },
    {
        "test_id": "nestjs_postgres_typeorm",
        "prompt": "How can a NestJS service connect to PostgreSQL using TypeORM?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": False,
    },
    {
        "test_id": "nestjs_redis_response_cache",
        "prompt": "How do I cache responses from NestJS API endpoints in Redis?",
        "canonical_id": "nestjs_redis_cache",
        "expected_match": True,
    },
    {
        "test_id": "france_capital_city_paraphrase",
        "prompt": "Which city is the capital of France?",
        "canonical_id": "capital_france",
        "expected_match": True,
    },
    {
        "test_id": "france_name_capital",
        "prompt": "Name France's capital city.",
        "canonical_id": "capital_france",
        "expected_match": True,
    },
    {
        "test_id": "france_largest_city",
        "prompt": "What is France's most populous city?",
        "canonical_id": "capital_france",
        "expected_match": False,
    },
    {
        "test_id": "germany_capital",
        "prompt": "What is the capital of Germany?",
        "canonical_id": "capital_france",
        "expected_match": False,
    },
    {
        "test_id": "python_reverse_string_code",
        "prompt": "Show me how to reverse a string using Python.",
        "canonical_id": "python_reverse_string",
        "expected_match": True,
    },
    {
        "test_id": "python_string_reverse_syntax",
        "prompt": "What is the Python syntax for reversing a string?",
        "canonical_id": "python_reverse_string",
        "expected_match": True,
    },
    {
        "test_id": "python_reverse_list",
        "prompt": "How can I reverse a list in Python?",
        "canonical_id": "python_reverse_string",
        "expected_match": False,
    },
    {
        "test_id": "python_palindrome_check",
        "prompt": "How do I check whether a string is a palindrome in Python?",
        "canonical_id": "python_reverse_string",
        "expected_match": False,
    },
]

REQUESTS = [
    {
        "index": 0,
        "canonical_id": "0",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I have a repo with 2 apps, an api and a web now i have added .github/workflow yaml files for basic build check api service is nestjs and web is nextjs service can i have github actions running on master after every merge request ? i am on a free tier",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 1,
        "canonical_id": "1",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I have a repo with 2 apps, an api and a web now i have added .github/workflow yaml files for basic build check api service is nestjs and web is nextjs service can i have github actions running on master after every merge request ? i am not on a paid account",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 2,
        "canonical_id": "2",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I have a monorepo containing a NestJS backend and a Next.js frontend. If I merge a pull request into the master branch, can GitHub Actions automatically run CI builds? I'm using GitHub's free plan.",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 3,
        "canonical_id": "3",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "Can I configure CI so that whenever code from a PR gets merged into master, GitHub automatically builds both my NestJS API and Next.js application? I don't have a paid GitHub subscription.",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 4,
        "canonical_id": "4",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "How can I deploy my NestJS API and Next.js application to AWS using GitHub Actions?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 5,
        "canonical_id": "5",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "How do I configure GitHub Actions to run my Jest unit tests whenever I push code?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 6,
        "canonical_id": "6",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "Does GitHub Actions consume free minutes when workflows run on private repositories?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 7,
        "canonical_id": "7",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "What is the best way to implement authentication in a NestJS application?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 8,
        "canonical_id": "8",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "How do I create an index on a PostgreSQL table containing millions of rows?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 9,
        "canonical_id": "9",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM asnwering questions for user queries",
            },
            {
                "role": "user",
                "content": "I hve a repo with 2 apps, an api and a web now i have adedd .github/workflow yaml files for basic biuld check api service is nestjs and web is nextjs service cna i hav github actions running on master after every mrege request ? i am on free tier",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 10,
        "canonical_id": "10",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I have a NestJS API and a Next.js frontend in the same repository. Can I configure GitHub Actions to build them whenever a pull request is merged into the main branch? I'm on the free GitHub plan.",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 11,
        "canonical_id": "11",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I'm using the free GitHub plan and have a monorepo containing a Next.js frontend and NestJS API. I want GitHub Actions to run my build checks every time a PR is merged into master. Is that possible?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 12,
        "canonical_id": "12",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I'm building a SaaS application. The repository is a monorepo with a NestJS API and Next.js frontend. We use npm, Node 20 and GitHub. I created separate workflow YAML files under .github/workflows. Can GitHub Actions run the API and frontend build checks automatically when a pull request is merged into master? I'm using the free plan.",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 13,
        "canonical_id": "13",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "How do I stop GitHub Actions from running when a pull request is merged into the master branch?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 14,
        "canonical_id": "14",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "I only want GitHub Actions to run when a pull request is opened, not when it is merged into master. How can I configure that?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 15,
        "canonical_id": "15",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "After merging a pull request, I want my backend and frontend build checks to execute automatically. I'm using GitHub's free tier. Can this be configured?",
            },
        ],
        "temperature": 0,
    },
    {
        "index": 16,
        "canonical_id": "16",
        "model": "gemini-3.1-flash-lite",
        "service_name": "google-genai",
        "messages": [
            {
                "role": "system",
                "content": "You are a general purpose LLM answering questions for user queries",
            },
            {
                "role": "user",
                "content": "How do I implement Redis caching in a NestJS application?",
            },
        ],
        "temperature": 0,
    },
]

REQUESTS.extend(
    {
        "index": len(REQUESTS) + index,
        "test_id": test["test_id"],
        "canonical_id": test["canonical_id"],
        "expected_match": test["expected_match"],
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
    for index, test in enumerate(THRESHOLD_TESTS)
)


def main() -> None:
    fieldnames = [
        "index",
        "test_id",
        "canonical_id",
        "expected_match",
        "prompt",
        "http_status",
        "x_cache",
        "actual_match",
        "top_1_score",
        "accepted",
        "response",
        "error",
    ]

    with open(RESULTS_CSV, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        with httpx.Client(timeout=120.0) as client:
            for index, payload in enumerate(REQUESTS, start=1):
                print(f"\n--- Request {index}/{len(REQUESTS)} ---")
                prompt = next(
                    (
                        message["content"]
                        for message in payload["messages"]
                        if message["role"] == "user"
                    ),
                    "",
                )
                row = {
                    "index": index,
                    "test_id": payload.get("test_id", ""),
                    "canonical_id": payload.get("canonical_id", ""),
                    "expected_match": payload.get("expected_match", ""),
                    "prompt": prompt,
                }

                print(f"Testing: [{payload['canonical_id']}] -> {prompt[:50]}...")
                try:
                    response = client.post(
                        API_URL,
                        json=payload,
                        headers={"x-api-key": API_KEY, "x-tenant-id": TENANT_ID},
                    )
                except httpx.HTTPError as error:
                    row["error"] = str(error)
                    writer.writerow(row)
                    print(f"Request failed: {error}")
                    time.sleep(4.2)
                    continue

                print(f"HTTP status: {response.status_code}")
                row["http_status"] = response.status_code
                row["x_cache"] = response.headers.get("X-Cache", "")
                try:
                    response_data = response.json()
                    cache_info = response_data.get("cache_info", {})
                    row["actual_match"] = cache_info.get("cache_hit", "")
                    row["top_1_score"] = cache_info.get(
                        "top_1_score", cache_info.get("score", "")
                    )
                    row["accepted"] = cache_info.get(
                        "accepted", cache_info.get("cache_hit", "")
                    )
                    row["response"] = json.dumps(response_data, ensure_ascii=False)
                    print(f"  -> Actual: {row['actual_match']}")
                except ValueError:
                    row["response"] = response.text
                    print(response.text)

                writer.writerow(row)
                time.sleep(4.2)

    print(f"\nResults written to {RESULTS_CSV}")


if __name__ == "__main__":
    main()
