"""Backwards-compatible re-export of the labelled prompt dataset.

The data itself now lives in ``tests/datasets/prompt_dataset.py`` because it is
test fixture data rather than application code. This shim keeps
``from app.scripts.test_dataset import TEST_DATASET`` working for existing
tooling; new code should import from ``tests.datasets``.
"""

from tests.datasets.prompt_dataset import TEST_DATASET

__all__ = ["TEST_DATASET"]
