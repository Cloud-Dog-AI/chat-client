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
def at_cross_service_timeout_seconds() -> int:
    return 600


@pytest.fixture(autouse=True)
def at_cross_service_env_overrides():
    api_key = str(
        os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY")
        or os.environ.get("IMAP_API_KEY")
        or os.environ.get("API_KEY")
        or ""
    ).strip()
    api_key_header = str(
        os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY_HEADER") or "X-API-Key"
    ).strip()
    file_base_url = str(
        os.environ.get("CLOUD_DOG__MCP__SERVERS__1__BASE_URL")
        or os.environ.get("TEST_FILE_MCP_BASE_URL")
        or "http://127.0.0.1:3002"
    ).strip()
    file_auth_token = str(
        os.environ.get("CLOUD_DOG__MCP__AT1_23__FILE_SERVER__AUTH_BEARER_TOKEN")
        or os.environ.get("CLOUD_DOG__MCP__SERVERS__1__AUTH_BEARER_TOKEN")
        or os.environ.get("FILE_MCP_API_KEY_PRIMARY")
        or ""
    ).strip()
    imap_base_url = str(
        os.environ.get("CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__BASE_URL")
        or os.environ.get("TEST_IMAP_MCP_BASE_URL")
        or "http://127.0.0.1:8072"
    ).strip()
    imap_auth_token = str(
        os.environ.get("CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__AUTH_BEARER_TOKEN")
        or os.environ.get("IMAP_API_KEY")
        or ""
    ).strip()

    original = {
        "CLOUD_DOG__APP__LOGFOLDER": os.environ.get("CLOUD_DOG__APP__LOGFOLDER"),
        "CLOUD_DOG__CLIENT_API__API_KEY": os.environ.get("CLOUD_DOG__CLIENT_API__API_KEY"),
        "CLOUD_DOG__CLIENT_API__API_KEY_HEADER": os.environ.get(
            "CLOUD_DOG__CLIENT_API__API_KEY_HEADER"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__NAME": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__NAME"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TRANSPORT": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TRANSPORT"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__BASE_URL": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__BASE_URL"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__MCP_PATH": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__MCP_PATH"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__VERIFY_TLS": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__VERIFY_TLS"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TIMEOUT_SECONDS": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TIMEOUT_SECONDS"
        ),
        "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__AUTH_BEARER_TOKEN": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__FILE_SERVER__AUTH_BEARER_TOKEN"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__NAME": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__NAME"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TRANSPORT": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TRANSPORT"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__BASE_URL": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__BASE_URL"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__MCP_PATH": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__MCP_PATH"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__VERIFY_TLS": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__VERIFY_TLS"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TIMEOUT_SECONDS": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TIMEOUT_SECONDS"
        ),
        "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__AUTH_BEARER_TOKEN": os.environ.get(
            "CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__AUTH_BEARER_TOKEN"
        ),
    }
    os.environ["CLOUD_DOG__APP__LOGFOLDER"] = "./logs"
    if api_key:
        os.environ["CLOUD_DOG__CLIENT_API__API_KEY"] = api_key
    os.environ["CLOUD_DOG__CLIENT_API__API_KEY_HEADER"] = api_key_header
    os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__NAME"] = "file-mcp"
    os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TRANSPORT"] = "streamable_http"
    if file_base_url:
        os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__BASE_URL"] = file_base_url
    os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__MCP_PATH"] = "/mcp"
    os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__VERIFY_TLS"] = "false"
    os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__TIMEOUT_SECONDS"] = "180"
    if file_auth_token:
        os.environ["CLOUD_DOG__MCP__AT1_23__FILE_SERVER__AUTH_BEARER_TOKEN"] = file_auth_token
    os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__NAME"] = "imap-mcp"
    os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TRANSPORT"] = "streamable_http"
    if imap_base_url:
        os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__BASE_URL"] = imap_base_url
    os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__MCP_PATH"] = "/mcp"
    os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__VERIFY_TLS"] = "false"
    os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__TIMEOUT_SECONDS"] = "180"
    if imap_auth_token:
        os.environ["CLOUD_DOG__MCP__AT1_23__IMAP_SERVER__AUTH_BEARER_TOKEN"] = imap_auth_token
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
