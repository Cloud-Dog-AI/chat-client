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
@pytest.mark.req("CS-002")


def test_ut1_6_api_kit_health_and_error_envelope(env_file):
    cfg = ConfigManager(env_file=env_file)
    app = create_app(cfg)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_payload = health.json()
        assert health_payload.get("status") == "ok"
        assert isinstance(health_payload.get("checks"), dict)
        assert health_payload.get("version")
        assert isinstance(health_payload.get("application"), dict)
        assert isinstance(health_payload.get("runtime"), dict)

        ready = client.get("/ready")
        assert ready.status_code == 200
        ready_payload = ready.json()
        assert ready_payload.get("status") in {"ok", "degraded"}
        assert isinstance(ready_payload.get("checks"), dict)
        assert ready_payload.get("version")

        live = client.get("/live")
        assert live.status_code == 200
        live_payload = live.json()
        assert live_payload.get("status") == "ok"
        assert live_payload.get("version")

        denied = client.post("/sessions", json={"metadata": {"suite": "ut1.6.api-kit"}})
        assert denied.status_code in {401, 403}
        denied_payload = denied.json()
        assert denied_payload.get("ok") is False
        errors = denied_payload.get("errors")
        assert isinstance(errors, list) and errors
        first = errors[0] or {}
        assert first.get("code")
        assert first.get("message")
        meta = denied_payload.get("meta") or {}
        assert meta.get("correlation_id")

# W28A-202 marker augmentation
_w28a_202_existing_pytestmark = globals().get("pytestmark", [])
if not isinstance(_w28a_202_existing_pytestmark, list):
    _w28a_202_existing_pytestmark = [_w28a_202_existing_pytestmark]
pytestmark = _w28a_202_existing_pytestmark + [pytest.mark.unit, pytest.mark.pure, pytest.mark.fast]

