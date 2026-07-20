# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
# Licensed under the Apache License, Version 2.0.

"""Fail-fast Python runtime contract for chat-client."""

from __future__ import annotations

import sys
from collections.abc import Sequence

MINIMUM_PYTHON = (3, 13)


def enforce_runtime(version_info: Sequence[int] | None = None) -> None:
    """Reject interpreters older than the supported Python 3.13 runtime."""

    observed = tuple((version_info or sys.version_info)[:2])
    if observed < MINIMUM_PYTHON:
        raise RuntimeError(
            "cloud-dog-chat-client requires Python 3.13 or newer; "
            f"observed {observed[0]}.{observed[1]}"
        )
