"""Nightly bin review: sends each bin to the vision model and stores proposals."""
import json

ALLOWED_KEYS = {"add"}


def parse(response_text):
    data = json.loads(response_text)
    return {k: v for k, v in data.items() if k in ALLOWED_KEYS}
