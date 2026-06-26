# Copyright 2026 Cloud-Dog, Viewdeck Engineering Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def at_web_search_timeout_seconds() -> int:
    return 1200


@pytest.fixture(autouse=True)
def at_web_search_env_overrides():
    api_key = str(
        os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY")
        or os.environ.get("IMAP_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    ).strip()
    api_key_header = str(
        os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY_HEADER") or "X-API-Key"
    ).strip()

    original = {
        "CLOUD_DOG__APP__LOGFOLDER": os.environ.get("CLOUD_DOG__APP__LOGFOLDER"),
        "CLOUD_DOG__CLIENT_API__API_KEY": os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY"),
        "CLOUD_DOG__CLIENT_API__API_KEY_HEADER": os.environ.get(
            "CLOUD_DOG__CLIENT_API__API_KEY_HEADER"
        ),
    }
    os.environ["CLOUD_DOG__APP__LOGFOLDER"] = "./logs"
    if api_key:
        os.environ["CLOUD_DOG__CLIENT_API__API_KEY"] = api_key
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY_HEADER"] = api_key_header
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
