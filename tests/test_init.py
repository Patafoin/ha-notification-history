"""Setup, live recording, statuses, WebSocket API and permissions."""
from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, async_capture_events

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.notification_history.const import DOMAIN

PHONE_OWNER = "owner-user-id"


def fire_notify(hass: HomeAssistant, service: str, service_data: dict[str, Any], context: Context | None = None) -> None:
    hass.bus.async_fire(
        EVENT_CALL_SERVICE, {"domain": "notify", "service": service, "service_data": service_data}, context=context
    )


class FakeLegacyService:
    """What notify/legacy.py keeps in hass.data['notify_services']."""

    def __init__(self, name: str, targets: dict[str, str] | None = None) -> None:
        self._service_name = name
        self.registered_targets = targets or {}


@pytest.fixture
async def phone(hass: HomeAssistant) -> MockConfigEntry:
    """A mobile_app device: legacy service notify.mobile_app_phone and entity notify.phone."""
    entry = MockConfigEntry(
        domain="mobile_app",
        title="Phone",
        data={"user_id": PHONE_OWNER, "webhook_id": "hook1", "device_id": "dev-app-id", "device_name": "phone"},
    )
    entry.add_to_hass(hass)
    hass.data["notify_services"] = {
        "mobile_app": [FakeLegacyService("mobile_app", {"mobile_app_phone": "hook1"})],
        "file": [FakeLegacyService("log_file")],
    }
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", "dev-app-id")}, name="Phone"
    )
    er.async_get(hass).async_get_or_create(
        "notify", "mobile_app", "dev-app-id", config_entry=entry, device_id=device.id, suggested_object_id="phone"
    )
    return entry


@pytest.fixture
async def loaded(hass: HomeAssistant, config_entry: MockConfigEntry, phone, recorder_mock=None) -> MockConfigEntry:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def ws_list(client, **kw) -> list[dict[str, Any]]:
    await client.send_json_auto_id({"type": f"{DOMAIN}/list", **kw})
    res = await client.receive_json()
    assert res["success"], res
    return res["result"]


async def test_records_live_calls_and_skips_excluded(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"title": "Alarme", "message": "Alarme activée", "data": {"url": "/lovelace/0"}})
    fire_notify(hass, "send_message", {"message": "Bonjour", "entity_id": ["notify.phone"]})
    fire_notify(hass, "log_file", {"message": "written to a file"})
    fire_notify(hass, "persistent_notification", {"message": "panel"})
    fire_notify(hass, "mobile_app_phone", {"message": "command_webview", "data": {"command": "/x"}})
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    result = await ws_list(client)
    items = result["notifications"]
    assert [n["message"] for n in items] == ["Bonjour", "Alarme activée"]
    # Legacy service and entity resolve to the same device and name.
    assert items[0]["target"] == "notify.phone" and items[1]["target"] == "notify.mobile_app_phone"
    assert items[0]["device_id"] == items[1]["device_id"] is not None
    assert items[0]["target_name"] == items[1]["target_name"] == "Phone"
    assert "owner" not in items[0] and "context_id" not in items[0]
    assert result["more"] is False


async def test_pagination_and_filters(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    for i in range(5):
        fire_notify(hass, "mobile_app_phone", {"message": f"m{i}"})
        await hass.async_block_till_done()
    fire_notify(hass, "alexa_media", {"message": "Echo"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    page = await ws_list(client, limit=2, targets=["notify.mobile_app_phone"])
    assert [n["message"] for n in page["notifications"]] == ["m4", "m3"] and page["more"]
    nxt = await ws_list(client, limit=10, targets=["notify.mobile_app_phone"], before_id=page["notifications"][-1]["id"])
    assert [n["message"] for n in nxt["notifications"]] == ["m2", "m1", "m0"]
    found = await ws_list(client, search="ech")
    assert [n["message"] for n in found["notifications"]] == ["Echo"]


async def test_tag_replace_and_clear_notification(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"title": "⛔ Défaut", "message": "Frigo", "data": {"tag": "frigo"}})
    await hass.async_block_till_done()
    fire_notify(hass, "mobile_app_phone", {"title": "✅ Résolu", "message": "Frigo", "data": {"tag": "frigo"}})
    await hass.async_block_till_done()
    fire_notify(hass, "send_message", {"message": "x", "entity_id": "notify.phone"})
    fire_notify(hass, "mobile_app_phone", {"message": "clear_notification", "data": {"tag": "frigo"}})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    items = {n["title"]: n["status"] for n in (await ws_list(client))["notifications"] if n["title"]}
    assert items == {"✅ Résolu": "removed", "⛔ Défaut": "removed"}


async def test_action_event_marks_notification(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(
        hass,
        "mobile_app_phone",
        {"message": "Porte ouverte", "data": {"actions": [{"action": "CLOSE_DOOR", "title": "Fermer"}]}},
    )
    await hass.async_block_till_done()
    # Tap on another user's device: ignored.
    hass.bus.async_fire("mobile_app_notification_action", {"action": "CLOSE_DOOR"}, context=Context(user_id="someone"))
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    assert (await ws_list(client))["notifications"][0]["status"] == "sent"
    hass.bus.async_fire("mobile_app_notification_action", {"action": "CLOSE_DOOR"}, context=Context(user_id=PHONE_OWNER))
    await hass.async_block_till_done()
    n = (await ws_list(client))["notifications"][0]
    assert n["status"] == "action" and n["action_title"] == "Fermer"


async def test_run_action_fires_event(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    events = async_capture_events(hass, "mobile_app_notification_action")
    fire_notify(
        hass,
        "mobile_app_phone",
        {
            "message": "Alarme ?",
            "data": {"tag": "alarm", "actions": [{"action": "ALARM_OFF", "title": "Off"}, {"action": "URI", "title": "Voir", "uri": "/x"}]},
        },
    )
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    record_id = (await ws_list(client))["notifications"][0]["id"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/run_action", "record_id": record_id, "action": "URI"})
    assert not (await client.receive_json())["success"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/run_action", "record_id": record_id, "action": "ALARM_OFF"})
    res = await client.receive_json()
    assert res["success"] and res["result"]["record"]["status"] == "action"
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data == {"action": "ALARM_OFF", "source": "notification_history", "tag": "alarm"}


async def test_non_admin_sees_only_own_devices(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "for the phone owner"})
    fire_notify(hass, "alexa_media", {"message": "speaker"})
    await hass.async_block_till_done()

    user = MockUser(id=PHONE_OWNER).add_to_hass(hass)
    token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(user, "https://example.com"))
    client = await hass_ws_client(hass, access_token=token)
    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["for the phone owner"]

    other = MockUser(id="other").add_to_hass(hass)
    token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(other, "https://example.com"))
    client = await hass_ws_client(hass, access_token=token)
    assert (await ws_list(client))["notifications"] == []


async def test_subscribe_pushes_new_records(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/subscribe", "targets": ["notify.mobile_app_phone"]})
    assert (await client.receive_json())["success"]
    fire_notify(hass, "alexa_media", {"message": "not this one"})
    fire_notify(hass, "mobile_app_phone", {"message": "this one"})
    await hass.async_block_till_done()
    msg = await client.receive_json()
    assert msg["type"] == "event" and msg["event"]["record"]["message"] == "this one"


async def test_selected_targets_only(hass: HomeAssistant, phone, hass_ws_client) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"targets": ["notify.alexa_media"], "max_days": 90, "max_count": 500, "logbook": False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    fire_notify(hass, "mobile_app_phone", {"message": "skip"})
    fire_notify(hass, "alexa_media", {"message": "keep"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["keep"]


async def test_clear_service_and_unload(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "a"})
    fire_notify(hass, "alexa_media", {"message": "b"})
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "clear", {"target": "notify.alexa_media"}, blocking=True)
    client = await hass_ws_client(hass)
    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["a"]
    assert await hass.config_entries.async_unload(loaded.entry_id)
    fire_notify(hass, "mobile_app_phone", {"message": "after unload"})
    await hass.async_block_till_done()
    await client.send_json_auto_id({"type": f"{DOMAIN}/list"})
    assert (await client.receive_json())["error"]["code"] == "not_loaded"
