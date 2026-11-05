---
name: api-contract
description: Instructions for managing, testing, and updating Echo Gate's OpenAPI snapshot contract tests. Use whenever adding, modifying, or removing FastAPI routes, parameters, or schemas.
---

# API Contract Testing & Snapshot Verification

Echo Gate enforces strict API contract consistency using an automated OpenAPI snapshot test.

## 1. How Snapshot Testing Works
- Test file: `tests/contract/test_openapi_snapshot.py`
- Canonical snapshot: `tests/contract/snapshots/openapi.json`
- The test compares the generated `app.openapi()` schema against the checked-in JSON snapshot.
- Any change to endpoint paths, request bodies, query parameters, response codes, or Pydantic schemas will trigger a diff failure during CI.

## 2. Procedure for Modifying Endpoints
1. Make your changes in `api/app/modules/<feature>/` (controller, schema, or route).
2. Run the test:
   ```bash
   poetry run pytest tests/contract/test_openapi_snapshot.py
   ```
3. If changes were intentional and verified to be backward-compatible:
   - Regenerate the snapshot (the test rewrites it on failure):
     ```bash
     poetry run pytest tests/contract/test_openapi_snapshot.py
     ```
   - Inspect `git diff tests/contract/snapshots/openapi.json` to verify only the expected additive changes were made.
   - Commit the updated snapshot alongside your code.
