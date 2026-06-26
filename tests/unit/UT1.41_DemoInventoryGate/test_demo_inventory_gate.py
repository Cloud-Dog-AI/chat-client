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
#
# Description: Login/SPA shell must NOT inject the demo-inventory panel (W28A-727-R5).
# Related tests: UT1.41

"""
UT1.41 - Login/SPA shell stays clean (W28A-727-R5 corruption reopen).

A prior lane (W28A-889-B-R2 / W28A-892) injected a server-side 'Cloud Dog demo
inventory' panel before the SPA root on EVERY page (including /login). The
coordinator reopened W28A-727-R5 because that injected block corrupted the
user-facing login/background surface.

This guard pins the corrected contract: ``serve_spa_index`` returns the built
``ui/dist/index.html`` verbatim - no demo-inventory section, no extra
same-origin /v1/* probes, nothing prepended before ``<div id="root">``. It fails
if any server-side login-surface injection is reintroduced.

Related Tasks: W28A-727-R5 (reopen), W28A-889-B-R2, W28A-892
"""

from __future__ import annotations

import pytest

from cloud_dog_chat_client.config import ConfigManager
from cloud_dog_chat_client.ui_spa import serve_spa_index

pytestmark = [pytest.mark.unit]


def _served_index() -> str:
    config = ConfigManager()
    resp = serve_spa_index(config)
    return resp.body.decode("utf-8")
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_served_index_has_no_demo_inventory_panel() -> None:
    html = _served_index()
    assert "cloud-dog-demo" not in html
    assert "Cloud Dog demo inventory" not in html
    # No same-origin principal/inventory probes injected into the login surface.
    assert "/v1/profiles" not in html
    assert "/auth/me" not in html
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_served_index_is_the_clean_spa_shell() -> None:
    html = _served_index()
    # The SPA root is present and nothing is prepended before it.
    assert '<div id="root"></div>' in html
    body_open = html.index("<body")
    root_idx = html.index('<div id="root">')
    between = html[body_open:root_idx]
    # Only the <body ...> open tag and whitespace may precede the SPA root.
    after_body_tag = between[between.index(">") + 1 :]
    assert after_body_tag.strip() == "", f"unexpected content before SPA root: {after_body_tag!r}"
@pytest.mark.UT
@pytest.mark.cli
@pytest.mark.req("FR-004")


def test_demo_inventory_panel_helper_is_removed() -> None:
    # The injection helper must not exist anymore.
    import cloud_dog_chat_client.ui_spa as ui_spa

    assert not hasattr(ui_spa, "_demo_inventory_panel")
