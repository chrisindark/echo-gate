TEST_DATASET = [
    # ============================================================
    # 1. GITHUB ACTIONS
    # ============================================================
    {
        "test_id": "gha_001",
        "prompt": "Can GitHub Actions run automatically after a pull request is merged into master?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": False,
    },
    {
        "test_id": "gha_002",
        "prompt": "Can GitHub Actions run on master whenever a PR gets merged?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_003",
        "prompt": "I want my GitHub workflow to execute every time a pull request is merged into the main branch. How do I configure that?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_004",
        "prompt": "How do I trigger a GitHub Actions workflow after a merge to the default branch?",
        "canonical_id": "github_actions_after_merge",
        "expected_match": True,
    },
    {
        "test_id": "gha_005",
        "prompt": "Can GitHub Actions run before a pull request is merged?",
        "canonical_id": "github_actions_before_merge",
        "expected_match": False,
    },
    {
        "test_id": "gha_006",
        "prompt": "How can I make GitHub Actions run whenever someone pushes directly to master?",
        "canonical_id": "github_actions_push_master",
        "expected_match": False,
    },
    # ============================================================
    # 2. PYTHON LIST REVERSAL
    # ============================================================
    {
        "test_id": "py_001",
        "prompt": "How can I reverse a list in Python?",
        "canonical_id": "python_reverse_list",
        "expected_match": False,
    },
    {
        "test_id": "py_002",
        "prompt": "What's the easiest way to reverse a Python list?",
        "canonical_id": "python_reverse_list",
        "expected_match": True,
    },
    {
        "test_id": "py_003",
        "prompt": "How do I get the elements of a list in reverse order in Python?",
        "canonical_id": "python_reverse_list",
        "expected_match": True,
    },
    {
        "test_id": "py_004",
        "prompt": "How do I reverse a list in JavaScript?",
        "canonical_id": "javascript_reverse_array",
        "expected_match": False,
    },
    {
        "test_id": "py_005",
        "prompt": "How can I sort a Python list in descending order?",
        "canonical_id": "python_sort_descending",
        "expected_match": False,
    },
    # ============================================================
    # 3. PYTHON DICTIONARY
    # ============================================================
    {
        "test_id": "dict_001",
        "prompt": "How do I check if a key exists in a Python dictionary?",
        "canonical_id": "python_dict_key_exists",
        "expected_match": False,
    },
    {
        "test_id": "dict_002",
        "prompt": "What's the Pythonic way to determine whether a dictionary contains a specific key?",
        "canonical_id": "python_dict_key_exists",
        "expected_match": True,
    },
    {
        "test_id": "dict_003",
        "prompt": "How can I check whether a Python dict has a particular key?",
        "canonical_id": "python_dict_key_exists",
        "expected_match": True,
    },
    {
        "test_id": "dict_004",
        "prompt": "How do I check if a value exists in a Python dictionary?",
        "canonical_id": "python_dict_value_exists",
        "expected_match": False,
    },
    # ============================================================
    # 4. JAVASCRIPT ARRAY
    # ============================================================
    {
        "test_id": "js_001",
        "prompt": "How do I remove duplicates from an array in JavaScript?",
        "canonical_id": "javascript_remove_array_duplicates",
        "expected_match": False,
    },
    {
        "test_id": "js_002",
        "prompt": "What's a simple way to eliminate duplicate values from a JavaScript array?",
        "canonical_id": "javascript_remove_array_duplicates",
        "expected_match": True,
    },
    {
        "test_id": "js_003",
        "prompt": "How can I get only unique elements from a JS array?",
        "canonical_id": "javascript_remove_array_duplicates",
        "expected_match": True,
    },
    {
        "test_id": "js_004",
        "prompt": "How do I sort an array in JavaScript?",
        "canonical_id": "javascript_sort_array",
        "expected_match": False,
    },
    # ============================================================
    # 5. NESTJS
    # ============================================================
    {
        "test_id": "nest_001",
        "prompt": "How do I create a GET endpoint in NestJS?",
        "canonical_id": "nestjs_create_get_endpoint",
        "expected_match": False,
    },
    {
        "test_id": "nest_002",
        "prompt": "What's the proper way to define a GET route in NestJS?",
        "canonical_id": "nestjs_create_get_endpoint",
        "expected_match": True,
    },
    {
        "test_id": "nest_003",
        "prompt": "How can I add a GET controller endpoint to my NestJS application?",
        "canonical_id": "nestjs_create_get_endpoint",
        "expected_match": True,
    },
    {
        "test_id": "nest_004",
        "prompt": "How do I create a POST endpoint in NestJS?",
        "canonical_id": "nestjs_create_post_endpoint",
        "expected_match": False,
    },
    {
        "test_id": "nest_005",
        "prompt": "How do I add authentication guards in NestJS?",
        "canonical_id": "nestjs_auth_guards",
        "expected_match": False,
    },
    # ============================================================
    # 6. NEXT.JS
    # ============================================================
    {
        "test_id": "next_001",
        "prompt": "How do I create a dynamic route in Next.js?",
        "canonical_id": "nextjs_dynamic_route",
        "expected_match": False,
    },
    {
        "test_id": "next_002",
        "prompt": "What's the syntax for dynamic routes in Next.js?",
        "canonical_id": "nextjs_dynamic_route",
        "expected_match": True,
    },
    {
        "test_id": "next_003",
        "prompt": "How can I define a dynamic URL parameter in a Next.js application?",
        "canonical_id": "nextjs_dynamic_route",
        "expected_match": True,
    },
    {
        "test_id": "next_004",
        "prompt": "How do I create a static route in Next.js?",
        "canonical_id": "nextjs_static_route",
        "expected_match": False,
    },
    # ============================================================
    # 7. DOCKER
    # ============================================================
    {
        "test_id": "docker_001",
        "prompt": "How do I build a Docker image from a Dockerfile?",
        "canonical_id": "docker_build_image",
        "expected_match": False,
    },
    {
        "test_id": "docker_002",
        "prompt": "What's the command to build an image using my Dockerfile?",
        "canonical_id": "docker_build_image",
        "expected_match": True,
    },
    {
        "test_id": "docker_003",
        "prompt": "How can I create a Docker image from a Dockerfile?",
        "canonical_id": "docker_build_image",
        "expected_match": True,
    },
    {
        "test_id": "docker_004",
        "prompt": "How do I start an existing Docker container?",
        "canonical_id": "docker_start_container",
        "expected_match": False,
    },
    {
        "test_id": "docker_005",
        "prompt": "How can I reduce the size of my Docker image?",
        "canonical_id": "docker_reduce_image_size",
        "expected_match": False,
    },
    # ============================================================
    # 8. KUBERNETES
    # ============================================================
    {
        "test_id": "k8s_001",
        "prompt": "How do I expose a Kubernetes deployment using a service?",
        "canonical_id": "kubernetes_expose_deployment",
        "expected_match": False,
    },
    {
        "test_id": "k8s_002",
        "prompt": "How can I expose my Kubernetes pods through a Service?",
        "canonical_id": "kubernetes_expose_deployment",
        "expected_match": True,
    },
    {
        "test_id": "k8s_003",
        "prompt": "What's the best way to expose a deployment in Kubernetes?",
        "canonical_id": "kubernetes_expose_deployment",
        "expected_match": True,
    },
    {
        "test_id": "k8s_004",
        "prompt": "How do I scale a Kubernetes deployment to five replicas?",
        "canonical_id": "kubernetes_scale_deployment",
        "expected_match": False,
    },
    # ============================================================
    # 9. SQL
    # ============================================================
    {
        "test_id": "sql_001",
        "prompt": "How do I find duplicate rows in SQL?",
        "canonical_id": "sql_find_duplicates",
        "expected_match": False,
    },
    {
        "test_id": "sql_002",
        "prompt": "What's a query for identifying duplicate records in a database table?",
        "canonical_id": "sql_find_duplicates",
        "expected_match": True,
    },
    {
        "test_id": "sql_003",
        "prompt": "How can I detect duplicate values using SQL?",
        "canonical_id": "sql_find_duplicates",
        "expected_match": True,
    },
    {
        "test_id": "sql_004",
        "prompt": "How do I delete duplicate rows from a SQL table?",
        "canonical_id": "sql_delete_duplicates",
        "expected_match": False,
    },
    {
        "test_id": "sql_005",
        "prompt": "How do I count the number of rows in a SQL table?",
        "canonical_id": "sql_count_rows",
        "expected_match": False,
    },
    # ============================================================
    # 10. POSTGRESQL
    # ============================================================
    {
        "test_id": "pg_001",
        "prompt": "How do I create an index in PostgreSQL?",
        "canonical_id": "postgres_create_index",
        "expected_match": False,
    },
    {
        "test_id": "pg_002",
        "prompt": "What's the syntax for adding an index to a Postgres table?",
        "canonical_id": "postgres_create_index",
        "expected_match": True,
    },
    {
        "test_id": "pg_003",
        "prompt": "How can I add a database index in PostgreSQL?",
        "canonical_id": "postgres_create_index",
        "expected_match": True,
    },
    {
        "test_id": "pg_004",
        "prompt": "How do I create a PostgreSQL database?",
        "canonical_id": "postgres_create_database",
        "expected_match": False,
    },
    # ============================================================
    # 11. REDIS
    # ============================================================
    {
        "test_id": "redis_001",
        "prompt": "How do I set a key with an expiration time in Redis?",
        "canonical_id": "redis_key_expiration",
        "expected_match": False,
    },
    {
        "test_id": "redis_002",
        "prompt": "How can I make a Redis key expire automatically?",
        "canonical_id": "redis_key_expiration",
        "expected_match": True,
    },
    {
        "test_id": "redis_003",
        "prompt": "What's the Redis command for setting a TTL on a key?",
        "canonical_id": "redis_key_expiration",
        "expected_match": True,
    },
    {
        "test_id": "redis_004",
        "prompt": "How do I delete a Redis key immediately?",
        "canonical_id": "redis_delete_key",
        "expected_match": False,
    },
    # ============================================================
    # 12. GIT
    # ============================================================
    {
        "test_id": "git_001",
        "prompt": "How do I undo my last Git commit?",
        "canonical_id": "git_undo_last_commit",
        "expected_match": False,
    },
    {
        "test_id": "git_002",
        "prompt": "What's the safest way to undo the most recent commit in Git?",
        "canonical_id": "git_undo_last_commit",
        "expected_match": True,
    },
    {
        "test_id": "git_003",
        "prompt": "How can I revert my latest Git commit?",
        "canonical_id": "git_undo_last_commit",
        "expected_match": True,
    },
    {
        "test_id": "git_004",
        "prompt": "How do I undo the last commit but keep the changes staged?",
        "canonical_id": "git_undo_commit_keep_changes",
        "expected_match": False,
    },
    {
        "test_id": "git_005",
        "prompt": "How do I create a new Git branch?",
        "canonical_id": "git_create_branch",
        "expected_match": False,
    },
    # ============================================================
    # 13. API DESIGN
    # ============================================================
    {
        "test_id": "api_001",
        "prompt": "Should I use PUT or PATCH when updating part of a resource?",
        "canonical_id": "api_put_vs_patch",
        "expected_match": False,
    },
    {
        "test_id": "api_002",
        "prompt": "What's the difference between PUT and PATCH for partial updates?",
        "canonical_id": "api_put_vs_patch",
        "expected_match": True,
    },
    {
        "test_id": "api_003",
        "prompt": "When should an API use PATCH instead of PUT?",
        "canonical_id": "api_put_vs_patch",
        "expected_match": True,
    },
    {
        "test_id": "api_004",
        "prompt": "What's the difference between POST and PUT?",
        "canonical_id": "api_post_vs_put",
        "expected_match": False,
    },
    # ============================================================
    # 14. HTTP
    # ============================================================
    {
        "test_id": "http_001",
        "prompt": "What does HTTP status code 404 mean?",
        "canonical_id": "http_404",
        "expected_match": False,
    },
    {
        "test_id": "http_002",
        "prompt": "Can you explain what a 404 response means in HTTP?",
        "canonical_id": "http_404",
        "expected_match": True,
    },
    {
        "test_id": "http_003",
        "prompt": "Why am I getting a 404 Not Found response?",
        "canonical_id": "http_404",
        "expected_match": True,
    },
    {
        "test_id": "http_004",
        "prompt": "What does HTTP 500 mean?",
        "canonical_id": "http_500",
        "expected_match": False,
    },
    # ============================================================
    # 15. JWT
    # ============================================================
    {
        "test_id": "jwt_001",
        "prompt": "What is a JWT and how does it work?",
        "canonical_id": "jwt_explanation",
        "expected_match": False,
    },
    {
        "test_id": "jwt_002",
        "prompt": "Can you explain JSON Web Tokens and how authentication with them works?",
        "canonical_id": "jwt_explanation",
        "expected_match": True,
    },
    {
        "test_id": "jwt_003",
        "prompt": "How does JWT-based authentication work?",
        "canonical_id": "jwt_explanation",
        "expected_match": True,
    },
    {
        "test_id": "jwt_004",
        "prompt": "How do I refresh an expired JWT?",
        "canonical_id": "jwt_refresh",
        "expected_match": False,
    },
    # ============================================================
    # 16. LINUX
    # ============================================================
    {
        "test_id": "linux_001",
        "prompt": "How do I find which process is using port 3000 on Linux?",
        "canonical_id": "linux_process_using_port",
        "expected_match": False,
    },
    {
        "test_id": "linux_002",
        "prompt": "How can I see what process has port 3000 open on Linux?",
        "canonical_id": "linux_process_using_port",
        "expected_match": True,
    },
    {
        "test_id": "linux_003",
        "prompt": "How do I identify the process listening on a particular port in Linux?",
        "canonical_id": "linux_process_using_port",
        "expected_match": True,
    },
    {
        "test_id": "linux_004",
        "prompt": "How do I kill a process in Linux?",
        "canonical_id": "linux_kill_process",
        "expected_match": False,
    },
    # ============================================================
    # 17. AWS
    # ============================================================
    {
        "test_id": "aws_001",
        "prompt": "How do I upload a file to an S3 bucket using AWS CLI?",
        "canonical_id": "aws_s3_upload_cli",
        "expected_match": False,
    },
    {
        "test_id": "aws_002",
        "prompt": "What's the AWS CLI command for uploading a file to S3?",
        "canonical_id": "aws_s3_upload_cli",
        "expected_match": True,
    },
    {
        "test_id": "aws_003",
        "prompt": "How can I copy a local file into an Amazon S3 bucket from the command line?",
        "canonical_id": "aws_s3_upload_cli",
        "expected_match": True,
    },
    {
        "test_id": "aws_004",
        "prompt": "How do I download a file from S3 using AWS CLI?",
        "canonical_id": "aws_s3_download_cli",
        "expected_match": False,
    },
    # ============================================================
    # 18. LLM / AI
    # ============================================================
    {
        "test_id": "llm_001",
        "prompt": "What is temperature in an LLM?",
        "canonical_id": "llm_temperature",
        "expected_match": False,
    },
    {
        "test_id": "llm_002",
        "prompt": "Can you explain what the temperature parameter does when generating text with an LLM?",
        "canonical_id": "llm_temperature",
        "expected_match": True,
    },
    {
        "test_id": "llm_003",
        "prompt": "How does changing an LLM's temperature affect its output?",
        "canonical_id": "llm_temperature",
        "expected_match": True,
    },
    {
        "test_id": "llm_004",
        "prompt": "What does top-p do in language model generation?",
        "canonical_id": "llm_top_p",
        "expected_match": False,
    },
    {
        "test_id": "llm_005",
        "prompt": "What is the difference between temperature and top-p?",
        "canonical_id": "llm_temperature_vs_top_p",
        "expected_match": False,
    },
    # ============================================================
    # 19. EMBEDDINGS / VECTOR DATABASES
    # ============================================================
    {
        "test_id": "vec_001",
        "prompt": "What is a vector embedding?",
        "canonical_id": "embedding_explanation",
        "expected_match": False,
    },
    {
        "test_id": "vec_002",
        "prompt": "Can you explain what embeddings are in machine learning?",
        "canonical_id": "embedding_explanation",
        "expected_match": True,
    },
    {
        "test_id": "vec_003",
        "prompt": "What does it mean to represent text as an embedding vector?",
        "canonical_id": "embedding_explanation",
        "expected_match": True,
    },
    {
        "test_id": "vec_004",
        "prompt": "How does cosine similarity work for embeddings?",
        "canonical_id": "cosine_similarity",
        "expected_match": False,
    },
    {
        "test_id": "vec_005",
        "prompt": "How is cosine similarity used to compare embedding vectors?",
        "canonical_id": "cosine_similarity",
        "expected_match": True,
    },
    # ============================================================
    # 20. QDRANT
    # ============================================================
    {
        "test_id": "qdrant_001",
        "prompt": "How do I perform a vector similarity search in Qdrant?",
        "canonical_id": "qdrant_similarity_search",
        "expected_match": False,
    },
    {
        "test_id": "qdrant_002",
        "prompt": "How can I search Qdrant for vectors that are semantically similar?",
        "canonical_id": "qdrant_similarity_search",
        "expected_match": True,
    },
    {
        "test_id": "qdrant_003",
        "prompt": "What's the Qdrant API for performing nearest-neighbor vector search?",
        "canonical_id": "qdrant_similarity_search",
        "expected_match": True,
    },
    {
        "test_id": "qdrant_004",
        "prompt": "How do I filter Qdrant results by payload fields?",
        "canonical_id": "qdrant_payload_filter",
        "expected_match": False,
    },
    {
        "test_id": "qdrant_005",
        "prompt": "How can I restrict Qdrant vector search using metadata filters?",
        "canonical_id": "qdrant_payload_filter",
        "expected_match": True,
    },
    # ============================================================
    # 21. OLLAMA
    # ============================================================
    {
        "test_id": "ollama_001",
        "prompt": "How do I generate embeddings using Ollama?",
        "canonical_id": "ollama_embeddings",
        "expected_match": False,
    },
    {
        "test_id": "ollama_002",
        "prompt": "What's the API endpoint for getting text embeddings from Ollama?",
        "canonical_id": "ollama_embeddings",
        "expected_match": True,
    },
    {
        "test_id": "ollama_003",
        "prompt": "How can I use Ollama to turn text into embedding vectors?",
        "canonical_id": "ollama_embeddings",
        "expected_match": True,
    },
    {
        "test_id": "ollama_004",
        "prompt": "How do I run an Ollama model locally?",
        "canonical_id": "ollama_run_model",
        "expected_match": False,
    },
    # ============================================================
    # 22. GENERAL KNOWLEDGE
    # ============================================================
    {
        "test_id": "geo_001",
        "prompt": "What is the capital of France?",
        "canonical_id": "france_capital",
        "expected_match": False,
    },
    {
        "test_id": "geo_002",
        "prompt": "Which city is the capital of France?",
        "canonical_id": "france_capital",
        "expected_match": True,
    },
    {
        "test_id": "geo_003",
        "prompt": "Tell me France's capital city.",
        "canonical_id": "france_capital",
        "expected_match": True,
    },
    {
        "test_id": "geo_004",
        "prompt": "What is the largest city in France by population?",
        "canonical_id": "france_largest_city",
        "expected_match": False,
    },
    # ============================================================
    # 23. SCIENCE
    # ============================================================
    {
        "test_id": "science_001",
        "prompt": "Why is the sky blue?",
        "canonical_id": "why_sky_blue",
        "expected_match": False,
    },
    {
        "test_id": "science_002",
        "prompt": "What causes the sky to appear blue during the day?",
        "canonical_id": "why_sky_blue",
        "expected_match": True,
    },
    {
        "test_id": "science_003",
        "prompt": "Why does sunlight make the daytime sky look blue?",
        "canonical_id": "why_sky_blue",
        "expected_match": True,
    },
    {
        "test_id": "science_004",
        "prompt": "Why does the sky turn red during sunset?",
        "canonical_id": "sunset_red_sky",
        "expected_match": False,
    },
    # ============================================================
    # 24. MATH
    # ============================================================
    {
        "test_id": "math_001",
        "prompt": "What is 15 percent of 240?",
        "canonical_id": "percentage_calculation",
        "expected_match": False,
    },
    {
        "test_id": "math_002",
        "prompt": "Calculate 15% of 240.",
        "canonical_id": "percentage_calculation",
        "expected_match": True,
    },
    {
        "test_id": "math_003",
        "prompt": "If I have 240 and need 15 percent of it, what is the result?",
        "canonical_id": "percentage_calculation",
        "expected_match": True,
    },
    {
        "test_id": "math_004",
        "prompt": "What is 20 percent of 240?",
        "canonical_id": "different_percentage_calculation",
        "expected_match": False,
    },
    # ============================================================
    # 25. WRITING
    # ============================================================
    {
        "test_id": "writing_001",
        "prompt": "Write a professional email asking for a meeting.",
        "canonical_id": "professional_meeting_email",
        "expected_match": False,
    },
    {
        "test_id": "writing_002",
        "prompt": "Can you draft a formal email requesting a meeting?",
        "canonical_id": "professional_meeting_email",
        "expected_match": True,
    },
    {
        "test_id": "writing_003",
        "prompt": "Please write a polite business email asking someone to schedule a meeting.",
        "canonical_id": "professional_meeting_email",
        "expected_match": True,
    },
    {
        "test_id": "writing_004",
        "prompt": "Write a casual text asking a friend to meet for coffee.",
        "canonical_id": "casual_coffee_message",
        "expected_match": False,
    },
    # ============================================================
    # 26. TRANSLATION
    # ============================================================
    {
        "test_id": "translation_001",
        "prompt": "Translate 'How are you?' into Spanish.",
        "canonical_id": "translate_how_are_you_spanish",
        "expected_match": False,
    },
    {
        "test_id": "translation_002",
        "prompt": "How do you say 'How are you?' in Spanish?",
        "canonical_id": "translate_how_are_you_spanish",
        "expected_match": True,
    },
    {
        "test_id": "translation_003",
        "prompt": "Give me the Spanish translation of 'How are you?'",
        "canonical_id": "translate_how_are_you_spanish",
        "expected_match": True,
    },
    {
        "test_id": "translation_004",
        "prompt": "Translate 'How are you?' into French.",
        "canonical_id": "translate_how_are_you_french",
        "expected_match": False,
    },
    # ============================================================
    # 27. TRAVEL
    # ============================================================
    {
        "test_id": "travel_001",
        "prompt": "What are some things to do in Tokyo?",
        "canonical_id": "things_to_do_tokyo",
        "expected_match": False,
    },
    {
        "test_id": "travel_002",
        "prompt": "What are the best attractions to visit in Tokyo?",
        "canonical_id": "things_to_do_tokyo",
        "expected_match": True,
    },
    {
        "test_id": "travel_003",
        "prompt": "Can you suggest places worth visiting in Tokyo?",
        "canonical_id": "things_to_do_tokyo",
        "expected_match": True,
    },
    {
        "test_id": "travel_004",
        "prompt": "What are some things to do in Kyoto?",
        "canonical_id": "things_to_do_kyoto",
        "expected_match": False,
    },
    # ============================================================
    # 28. FOOD
    # ============================================================
    {
        "test_id": "food_001",
        "prompt": "How do I make homemade pizza dough?",
        "canonical_id": "pizza_dough",
        "expected_match": False,
    },
    {
        "test_id": "food_002",
        "prompt": "What's a good recipe for making pizza dough at home?",
        "canonical_id": "pizza_dough",
        "expected_match": True,
    },
    {
        "test_id": "food_003",
        "prompt": "How can I prepare pizza dough from scratch?",
        "canonical_id": "pizza_dough",
        "expected_match": True,
    },
    {
        "test_id": "food_004",
        "prompt": "How do I make homemade pasta dough?",
        "canonical_id": "pasta_dough",
        "expected_match": False,
    },
    # ============================================================
    # 29. FINANCE / GENERAL
    # ============================================================
    {
        "test_id": "finance_001",
        "prompt": "What is compound interest?",
        "canonical_id": "compound_interest",
        "expected_match": False,
    },
    {
        "test_id": "finance_002",
        "prompt": "Can you explain how compound interest works?",
        "canonical_id": "compound_interest",
        "expected_match": True,
    },
    {
        "test_id": "finance_003",
        "prompt": "How does interest earning on previous interest work?",
        "canonical_id": "compound_interest",
        "expected_match": True,
    },
    {
        "test_id": "finance_004",
        "prompt": "What is simple interest?",
        "canonical_id": "simple_interest",
        "expected_match": False,
    },
    # ============================================================
    # 30. PRODUCTIVITY
    # ============================================================
    {
        "test_id": "productivity_001",
        "prompt": "How can I stay focused while working from home?",
        "canonical_id": "work_from_home_focus",
        "expected_match": False,
    },
    {
        "test_id": "productivity_002",
        "prompt": "What are some ways to improve concentration when working remotely?",
        "canonical_id": "work_from_home_focus",
        "expected_match": True,
    },
    {
        "test_id": "productivity_003",
        "prompt": "How do I avoid distractions while working at home?",
        "canonical_id": "work_from_home_focus",
        "expected_match": True,
    },
    {
        "test_id": "productivity_004",
        "prompt": "How can I stay focused while studying for exams?",
        "canonical_id": "study_focus",
        "expected_match": False,
    },
    # ============================================================
    # 31. HARD NEGATIVES — SAME WORDS, DIFFERENT INTENT
    # ============================================================
    {
        "test_id": "hard_001",
        "prompt": "How do I reverse a list in Python?",
        "canonical_id": "python_reverse_list_hard",
        "expected_match": False,
    },
    {
        "test_id": "hard_002",
        "prompt": "How do I reverse a string in Python?",
        "canonical_id": "python_reverse_string",
        "expected_match": False,
    },
    {
        "test_id": "hard_003",
        "prompt": "How do I create an index in PostgreSQL?",
        "canonical_id": "postgres_index_create_hard",
        "expected_match": False,
    },
    {
        "test_id": "hard_004",
        "prompt": "How do I remove an index in PostgreSQL?",
        "canonical_id": "postgres_index_remove",
        "expected_match": False,
    },
    {
        "test_id": "hard_005",
        "prompt": "How do I delete a Docker container?",
        "canonical_id": "docker_delete_container",
        "expected_match": False,
    },
    {
        "test_id": "hard_006",
        "prompt": "How do I delete a Docker image?",
        "canonical_id": "docker_delete_image",
        "expected_match": False,
    },
    {
        "test_id": "hard_007",
        "prompt": "How do I create a GitHub Actions workflow?",
        "canonical_id": "github_actions_create_workflow",
        "expected_match": False,
    },
    {
        "test_id": "hard_008",
        "prompt": "How do I delete a GitHub Actions workflow?",
        "canonical_id": "github_actions_delete_workflow",
        "expected_match": False,
    },
    # ============================================================
    # 32. HARD NEGATIVES — DIFFERENT VALUES
    # ============================================================
    {
        "test_id": "values_001",
        "prompt": "What is 10% of 500?",
        "canonical_id": "percentage_10_500",
        "expected_match": False,
    },
    {
        "test_id": "values_002",
        "prompt": "What is 10% of 600?",
        "canonical_id": "percentage_10_600",
        "expected_match": False,
    },
    {
        "test_id": "values_003",
        "prompt": "How do I find process using port 3000?",
        "canonical_id": "port_3000",
        "expected_match": False,
    },
    {
        "test_id": "values_004",
        "prompt": "How do I find process using port 8080?",
        "canonical_id": "port_8080",
        "expected_match": False,
    },
    {
        "test_id": "values_005",
        "prompt": "How do I deploy five replicas in Kubernetes?",
        "canonical_id": "k8s_five_replicas",
        "expected_match": False,
    },
    {
        "test_id": "values_006",
        "prompt": "How do I deploy ten replicas in Kubernetes?",
        "canonical_id": "k8s_ten_replicas",
        "expected_match": False,
    },
    # ============================================================
    # 33. HARD NEGATIVES — TEMPORAL DIFFERENCES
    # ============================================================
    {
        "test_id": "time_001",
        "prompt": "What's the weather in London today?",
        "canonical_id": "weather_london_today",
        "expected_match": False,
    },
    {
        "test_id": "time_002",
        "prompt": "What's the weather in London tomorrow?",
        "canonical_id": "weather_london_tomorrow",
        "expected_match": False,
    },
    {
        "test_id": "time_003",
        "prompt": "What are today's top technology news stories?",
        "canonical_id": "tech_news_today",
        "expected_match": False,
    },
    {
        "test_id": "time_004",
        "prompt": "What were yesterday's top technology news stories?",
        "canonical_id": "tech_news_yesterday",
        "expected_match": False,
    },
    # ============================================================
    # 34. HARD NEGATIVES — ENTITY DIFFERENCES
    # ============================================================
    {
        "test_id": "entity_001",
        "prompt": "How do I deploy a Next.js application to Vercel?",
        "canonical_id": "nextjs_vercel_deploy",
        "expected_match": False,
    },
    {
        "test_id": "entity_002",
        "prompt": "How do I deploy a Next.js application to AWS?",
        "canonical_id": "nextjs_aws_deploy",
        "expected_match": False,
    },
    {
        "test_id": "entity_003",
        "prompt": "How do I connect a NestJS application to PostgreSQL?",
        "canonical_id": "nestjs_postgres",
        "expected_match": False,
    },
    {
        "test_id": "entity_004",
        "prompt": "How do I connect a NestJS application to MongoDB?",
        "canonical_id": "nestjs_mongodb",
        "expected_match": False,
    },
    # ============================================================
    # 35. INFORMAL / TYPO PARAPHRASES
    # ============================================================
    {
        "test_id": "informal_001",
        "prompt": "how do i reverse a list python",
        "canonical_id": "python_reverse_informal",
        "expected_match": False,
    },
    {
        "test_id": "informal_002",
        "prompt": "whats the way to reverse a list in python",
        "canonical_id": "python_reverse_informal",
        "expected_match": True,
    },
    {
        "test_id": "informal_003",
        "prompt": "how can i revers a list in pyhton",
        "canonical_id": "python_reverse_informal",
        "expected_match": True,
    },
    {
        "test_id": "informal_004",
        "prompt": "python list reverse how?",
        "canonical_id": "python_reverse_informal",
        "expected_match": True,
    },
    # ============================================================
    # 36. LONG VS SHORT PARAPHRASES
    # ============================================================
    {
        "test_id": "long_001",
        "prompt": "How do I reverse a Python list?",
        "canonical_id": "reverse_list_long_short",
        "expected_match": False,
    },
    {
        "test_id": "long_002",
        "prompt": "I'm working on a Python application and I have a list of values. I don't want to manually loop through it or create another complicated data structure. I simply need to change the ordering so that the last element becomes the first and the first becomes the last. What's the standard Python approach for reversing the list?",
        "canonical_id": "reverse_list_long_short",
        "expected_match": True,
    },
    # ============================================================
    # 37. COMPLETELY UNRELATED
    # ============================================================
    {
        "test_id": "unrelated_001",
        "prompt": "What is the capital of Japan?",
        "canonical_id": "capital_japan",
        "expected_match": False,
    },
    {
        "test_id": "unrelated_002",
        "prompt": "How do black holes form?",
        "canonical_id": "black_holes",
        "expected_match": False,
    },
    {
        "test_id": "unrelated_003",
        "prompt": "Give me a recipe for chocolate cake.",
        "canonical_id": "chocolate_cake",
        "expected_match": False,
    },
    {
        "test_id": "unrelated_004",
        "prompt": "Write a poem about the ocean.",
        "canonical_id": "ocean_poem",
        "expected_match": False,
    },
    {
        "test_id": "unrelated_005",
        "prompt": "Explain how photosynthesis works.",
        "canonical_id": "photosynthesis",
        "expected_match": False,
    },
]
