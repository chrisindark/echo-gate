"""Shared encoder-serving layer for the internal products.

Import nothing from here in a consumer: this package is reached over HTTP, and
`contract/INFERENCE_CONTRACT.md` is the only interface that exists.
"""

__version__ = "0.1.0"
