from __future__ import annotations

import time

from tests.helpers.w28a751_live import login, request
import pytest


def _admin_json(method: str, path: str, *, body: dict | None = None, client):
    resp = request(method, path, body=body, client=client)
    assert resp.status in {200, 201}, f"{method} {path} failed: {resp.status} {resp.body}"
    return resp.json()
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-007")


def test_t1_common_idam_cookie_login_and_config_reads():
    client = login("admin")
    me = request("GET", "/auth/me", client=client)
    assert me.status == 200
    payload = me.json()
    assert "admin" in payload["user"]["roles"]
    assert "*" in payload["user"]["permissions"]

    cfg = request("GET", "/ui/config/tree", client=client)
    assert cfg.status == 200
    assert "redacted" in cfg.body.lower()
    assert "OrangeRiverTable" not in cfg.body

    profiles = request("GET", "/v1/profiles", client=client)
    assert profiles.status == 200
    assert "profiles" in profiles.json()
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-007")


def test_t2_rbac_read_only_denies_data_writes_but_allows_reads():
    readonly = login("read-only")
    me = request("GET", "/auth/me", client=readonly)
    assert me.status == 200
    assert "read-only" in me.json()["user"]["roles"]

    read_profiles = request("GET", "/v1/profiles", client=readonly)
    assert read_profiles.status == 200

    denied = request(
        "PUT",
        "/sessions/w28a751-readonly/preferences",
        body={"selected_mcp_server_indices": [0]},
        client=readonly,
    )
    assert denied.status == 403
    assert "read-only" in denied.body

    readwrite = login("read-write")
    allowed = request(
        "PUT",
        "/sessions/w28a751-readwrite/preferences",
        body={"selected_mcp_server_indices": [0]},
        client=readwrite,
    )
    assert allowed.status in {200, 404}, allowed.body
@pytest.mark.AT
@pytest.mark.cli
@pytest.mark.req("FR-007")


def test_t3_business_cascade_api_mcp_a2a_surfaces():
    admin = login("admin")
    suffix = str(int(time.time() * 1000))
    user_id = f"w28a751-user-{suffix}"
    group_id = f"w28a751-group-{suffix}"
    key_id = f"w28a751-key-{suffix}"
    promoted_profile = f"w28a751-promoted-{suffix}"
    denied_profile = f"w28a751-denied-{suffix}"
    created_key_id = key_id

    try:
        _admin_json(
            "POST",
            "/v1/users",
            client=admin,
            body={
                "user_id": user_id,
                "display_name": user_id,
                "email": f"{user_id}@w28a751.invalid",
                "role": "viewer",
                "status": "active",
                "group_ids": [],
            },
        )
        group = _admin_json(
            "POST",
            "/v1/groups",
            client=admin,
            body={"group_id": group_id, "name": group_id, "roles": ["admin"], "member_user_ids": [user_id]},
        )["group"]
        assert user_id in group["member_user_ids"]

        key = _admin_json(
            "POST",
            "/v1/api-keys",
            client=admin,
            body={"key_id": key_id, "user_id": user_id, "name": key_id, "scopes": []},
        )["api_key"]
        api_key = key["api_key"]
        created_key_id = key["key_id"]
        headers = {"X-API-Key": api_key, "X-Admin-Key": api_key, "X-User": user_id}

        promoted = request(
            "POST",
            "/api/v1/profiles",
            headers=headers,
            body={"profile_id": promoted_profile, "name": promoted_profile, "description": "W28A-751 cascade promoted"},
        )
        if promoted.status == 404:
            promoted = request(
                "POST",
                "/v1/profiles",
                headers=headers,
                body={"profile_id": promoted_profile, "name": promoted_profile, "description": "W28A-751 cascade promoted"},
            )
        assert promoted.status in {200, 201}, promoted.body

        tools = request("GET", "/api/mcp/admin/tools", client=admin)
        assert tools.status == 200
        assert any(item["name"] == "profile_create" for item in tools.json()["tools"])

        events = request("GET", "/api/a2a/events", client=admin)
        assert events.status == 200
        assert "events" in events.json()

        _admin_json(
            "PUT",
            f"/v1/groups/{group_id}",
            client=admin,
            body={"group_id": group_id, "name": group_id, "roles": ["admin"], "member_user_ids": []},
        )

        revoked = request(
            "POST",
            "/api/v1/profiles",
            headers=headers,
            body={"profile_id": denied_profile, "name": denied_profile, "description": "W28A-751 cascade denied"},
        )
        if revoked.status == 404:
            revoked = request(
                "POST",
                "/v1/profiles",
                headers=headers,
                body={"profile_id": denied_profile, "name": denied_profile, "description": "W28A-751 cascade denied"},
            )
        assert revoked.status == 403, revoked.body
    finally:
        request("DELETE", f"/v1/profiles/{promoted_profile}", client=admin)
        request("DELETE", f"/v1/profiles/{denied_profile}", client=admin)
        request("DELETE", f"/v1/api-keys/{created_key_id}", client=admin)
        request("DELETE", f"/v1/groups/{group_id}", client=admin)
        request("DELETE", f"/v1/users/{user_id}", client=admin)
