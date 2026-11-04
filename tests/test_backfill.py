"""One-off import of past notifications from the recorder."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import Context, HomeAssistant

from custom_components.notification_history.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations) -> None:
    """Same as conftest, but the recorder must be set up before hass."""


@pytest.fixture
def options() -> dict:
    return {"targets": [], "max_days": 90, "max_count": 500, "logbook": False}


async def test_imports_past_calls_once(hass: HomeAssistant, hass_ws_client, hass_storage, options) -> None:
    ctx = Context(user_id="0123456789abcdef0123456789abcdef")
    for service, data in (
        ("mobile_app_phone", {"title": "Sauvegarde", "message": "Sauvegarde terminée"}),
        ("mobile_app_phone", {"message": "command_webview", "data": {"command": "/x"}}),
        ("send_message", {"message": "Il fait 19 degrés", "entity_id": ["notify.echo"]}),
    ):
        hass.bus.async_fire(EVENT_CALL_SERVICE, {"domain": "notify", "service": service, "service_data": data}, context=ctx)
    hass.bus.async_fire(EVENT_CALL_SERVICE, {"domain": "light", "service": "turn_on", "service_data": {}})
    await async_wait_recording_done(hass)

    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    manager = hass.data[DOMAIN]
    # The background import may still run right after setup.
    for _ in range(50):
        if manager.store.backfilled:
            break
        await hass.async_block_till_done(wait_background_tasks=True)
    assert manager.store.backfilled

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/list"})
    items = (await client.receive_json())["result"]["notifications"]
    assert [(n["target"], n["message"]) for n in items] == [
        ("notify.echo", "Il fait 19 degrés"),
        ("notify.mobile_app_phone", "Sauvegarde terminée"),
    ]
    assert manager.store.records[0]["sent_by"] == ctx.user_id
    assert manager.store.records[0]["id"].startswith(ctx.id)

    # Reloading does not import again (and does not duplicate).
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hass.data[DOMAIN].store.records) == 2
