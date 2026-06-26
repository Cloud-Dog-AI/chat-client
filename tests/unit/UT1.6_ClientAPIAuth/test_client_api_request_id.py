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
import pytest

from fastapi.testclient import TestClient

from cloud_dog_chat_client.api.server import create_app
from cloud_dog_chat_client.config import ConfigManager
@pytest.mark.UT
@pytest.mark.api
@pytest.mark.req("FR-006")


def test_ut1_6_request_id_header_is_propagated(env_file, monkeypatch):
    monkeypatch.setenv("CLOUD_DOG__CLIENT_API__REQUEST_ID_HEADER", "X-Correlation-Id")

    cfg = ConfigManager(env_file=env_file)
    app = create_app(cfg)

    with TestClient(app) as client:
        generated = client.get("/health")
        assert generated.status_code == 200
        generated_id = str(generated.headers.get("X-Correlation-Id") or "").strip()
        assert generated_id

        supplied = client.get(
            "/health", headers={"X-Correlation-Id": "cid-unit-test-1"}
        )
        assert supplied.status_code == 200
        value = str(supplied.headers.get("X-Correlation-Id") or "")
        parts = [p.strip() for p in value.split(",") if p.strip()]
        assert "cid-unit-test-1" in parts

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

