"""Setup, live recording, statuses, WebSocket API and permissions."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser, async_capture_events

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Context, CoreState, HomeAssistant
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.notification_history.const import DOMAIN
from custom_components.notification_history.targets import resolve

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


async def test_reload_after_started_logs_no_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, phone, caplog: pytest.LogCaptureFixture
) -> None:
    """Set up before HA has started, then unload: no 'Unable to remove unknown job listener'."""
    hass.set_state(CoreState.starting)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text


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


async def test_clear_service_is_admin_only(hass: HomeAssistant, loaded, hass_read_only_user: MockUser) -> None:
    with pytest.raises(Unauthorized):
        await hass.services.async_call(DOMAIN, "clear", {}, blocking=True, context=Context(user_id=hass_read_only_user.id))


async def test_legacy_mobile_app_service_resolved_from_device_name(hass: HomeAssistant) -> None:
    """notify.mobile_app_<slug of the device name> is matched without reading the webhook id."""
    entry = MockConfigEntry(
        domain="mobile_app",
        title="Pixel de Lou",
        data={"user_id": PHONE_OWNER, "device_id": "pixel-id", "device_name": "Pixel de Lou"},
    )
    entry.add_to_hass(hass)
    hass.data["notify_services"] = {"mobile_app": [FakeLegacyService("mobile_app", {"mobile_app_pixel_de_lou": "secret"})]}
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={("mobile_app", "pixel-id")})
    info = resolve(hass, "notify.mobile_app_pixel_de_lou")
    assert (info["platform"], info["owner"], info["name"], info["device_id"]) == ("mobile_app", PHONE_OWNER, "Pixel de Lou", device.id)


@pytest.fixture
async def with_persistent(hass: HomeAssistant, phone) -> MockConfigEntry:
    """Entry with the persistent notifications option on."""
    assert await async_setup_component(hass, "persistent_notification", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={"targets": [], "max_days": 90, "max_count": 500, "logbook": False, "persistent": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_persistent_notifications_recorded(hass: HomeAssistant, with_persistent, hass_ws_client) -> None:
    persistent_notification.async_create(hass, "Washing machine done", title="Laundry", notification_id="laundry")
    await hass.services.async_call(
        "persistent_notification", "create", {"message": "From an automation"}, blocking=True
    )
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    items = (await ws_list(client))["notifications"]
    assert [(n["title"], n["message"]) for n in items] == [(None, "From an automation"), ("Laundry", "Washing machine done")]
    assert {n["target"] for n in items} == {"persistent_notification"} and items[1]["status"] == "sent"

    # Same id again: the bell shows the new one only.
    persistent_notification.async_create(hass, "Second load done", title="Laundry", notification_id="laundry")
    await hass.async_block_till_done()
    items = (await ws_list(client, search="Laundry"))["notifications"]
    assert [(n["message"], n["status"]) for n in items] == [("Second load done", "sent"), ("Washing machine done", "replaced")]

    # Dismissed from the bell.
    persistent_notification.async_dismiss(hass, "laundry")
    await hass.async_block_till_done()
    assert (await ws_list(client, search="Second"))["notifications"][0]["status"] == "cleared"


async def test_persistent_notifications_admin_only(hass: HomeAssistant, with_persistent, hass_ws_client) -> None:
    persistent_notification.async_create(hass, "Login attempt failed", notification_id="http-login")
    await hass.async_block_till_done()
    user = MockUser(id=PHONE_OWNER).add_to_hass(hass)
    token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(user, "https://example.com"))
    client = await hass_ws_client(hass, access_token=token)
    assert (await ws_list(client))["notifications"] == []


async def test_persistent_notifications_off_by_default(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    assert await async_setup_component(hass, "persistent_notification", {})
    persistent_notification.async_create(hass, "Not recorded", notification_id="x")
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    assert (await ws_list(client))["notifications"] == []


async def test_tts_command_not_recorded(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    """Android speaks data.tts_text for message TTS: nothing is shown, like the other commands."""
    fire_notify(hass, "mobile_app_phone", {"message": "TTS", "data": {"tts_text": "Dinner is ready"}})
    fire_notify(hass, "mobile_app_phone", {"message": "shown"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["shown"]


async def test_old_tts_records_dropped_on_load(hass: HomeAssistant, config_entry, phone, hass_storage, hass_ws_client) -> None:
    base = {"target": "notify.mobile_app_phone", "title": None, "status": "sent", "actions": []}
    hass_storage[DOMAIN] = {
        "version": 1,
        "key": DOMAIN,
        "data": {
            "backfilled": True,
            "records": [
                {**base, "id": "a", "ts": 1e9 + 1, "message": "TTS"},
                {**base, "id": "b", "ts": 1e9 + 2, "message": "kept"},
            ],
        },
    }
    with patch("time.time", return_value=1e9 + 10):
        config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        client = await hass_ws_client(hass)
        assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["kept"]


async def test_notify_group_not_recorded_twice(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    """A group calls notify again for each member: only the member is recorded."""
    hass.data["notify_services"]["group"] = [FakeLegacyService("family")]
    fire_notify(hass, "family", {"message": "Dinner"})
    fire_notify(hass, "mobile_app_phone", {"message": "Dinner"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    assert [n["target"] for n in (await ws_list(client))["notifications"]] == ["notify.mobile_app_phone"]


async def test_channel_recorded_filtered_and_listed(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "Person at the door", "data": {"channel": "Frigate"}})
    fire_notify(hass, "mobile_app_phone", {"message": "Backup done", "data": {"channel": "Server"}})
    fire_notify(hass, "alexa_media", {"message": "No channel"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    items = (await ws_list(client, channels=["Frigate"]))["notifications"]
    assert [(n["message"], n["channel"]) for n in items] == [("Person at the door", "Frigate")]
    await client.send_json_auto_id({"type": f"{DOMAIN}/channels"})
    assert (await client.receive_json())["result"] == {"channels": ["Frigate", "Server"]}

    # A non-admin only gets the channels of their own devices.
    fire_notify(hass, "alexa_media", {"message": "Speaker", "data": {"channel": "Speakers"}})
    await hass.async_block_till_done()
    user = MockUser(id="other").add_to_hass(hass)
    token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(user, "https://example.com"))
    other = await hass_ws_client(hass, access_token=token)
    await other.send_json_auto_id({"type": f"{DOMAIN}/channels"})
    assert (await other.receive_json())["result"] == {"channels": []}


async def test_group_filter_and_list(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "Motion", "data": {"group": "cameras"}})
    fire_notify(hass, "mobile_app_phone", {"message": "Backup", "data": {"group": "server"}})
    fire_notify(hass, "mobile_app_phone", {"message": "No group"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    items = (await ws_list(client, groups=["cameras"]))["notifications"]
    assert [(n["message"], n["group"]) for n in items] == [("Motion", "cameras")]
    await client.send_json_auto_id({"type": f"{DOMAIN}/groups"})
    assert (await client.receive_json())["result"] == {"groups": ["cameras", "server"]}


async def test_delete_is_soft_and_can_be_restored(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "first"})
    fire_notify(hass, "mobile_app_phone", {"message": "second"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    second, first = (await ws_list(client))["notifications"]

    sub = await hass_ws_client(hass)
    await sub.send_json_auto_id({"type": f"{DOMAIN}/subscribe"})
    assert (await sub.receive_json())["success"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/delete", "record_id": first["id"]})
    res = await client.receive_json()
    assert res["success"] and res["result"]["record"]["deleted_ts"]
    pushed = await sub.receive_json()
    assert pushed["event"]["record"]["id"] == first["id"] and pushed["event"]["record"]["deleted_ts"]

    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["second"]
    listed = (await ws_list(client, include_deleted=True))["notifications"]
    assert [(n["message"], bool(n["deleted_ts"])) for n in listed] == [("second", False), ("first", True)]
    # Paging past a deleted notification still works.
    assert (await ws_list(client, limit=1))["more"] is False

    await client.send_json_auto_id({"type": f"{DOMAIN}/delete", "record_id": first["id"], "deleted": False})
    res = await client.receive_json()
    assert res["success"] and res["result"]["record"]["deleted_ts"] is None
    assert [n["message"] for n in (await ws_list(client))["notifications"]] == ["second", "first"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/delete", "record_id": "nope"})
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_delete_needs_to_see_the_notification(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "alexa_media", {"message": "speaker"})
    await hass.async_block_till_done()
    admin = await hass_ws_client(hass)
    record_id = (await ws_list(admin))["notifications"][0]["id"]

    other = MockUser(id="other").add_to_hass(hass)
    token = hass.auth.async_create_access_token(await hass.auth.async_create_refresh_token(other, "https://example.com"))
    client = await hass_ws_client(hass, access_token=token)
    await client.send_json_auto_id({"type": f"{DOMAIN}/delete", "record_id": record_id})
    assert (await client.receive_json())["error"]["code"] == "not_found"
    assert len((await ws_list(admin))["notifications"]) == 1


async def test_exclude_channels_and_groups(hass: HomeAssistant, loaded, hass_ws_client) -> None:
    fire_notify(hass, "mobile_app_phone", {"message": "Motion", "data": {"group": "cameras", "channel": "Frigate"}})
    fire_notify(hass, "mobile_app_phone", {"message": "Backup", "data": {"group": "server", "channel": "Server"}})
    fire_notify(hass, "mobile_app_phone", {"message": "Plain"})
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    messages = lambda res: [n["message"] for n in res["notifications"]]  # noqa: E731
    assert messages(await ws_list(client, exclude_groups=["cameras"])) == ["Plain", "Backup"]
    assert messages(await ws_list(client, exclude_channels=["Frigate", "Server"])) == ["Plain"]

    sub = await hass_ws_client(hass)
    await sub.send_json_auto_id({"type": f"{DOMAIN}/subscribe", "exclude_groups": ["cameras"]})
    assert (await sub.receive_json())["success"]
    fire_notify(hass, "mobile_app_phone", {"message": "Motion 2", "data": {"group": "cameras"}})
    fire_notify(hass, "mobile_app_phone", {"message": "Other", "data": {"group": "misc"}})
    await hass.async_block_till_done()
    assert (await sub.receive_json())["event"]["record"]["message"] == "Other"
