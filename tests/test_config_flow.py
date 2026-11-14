"""Config and options flow."""
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.notification_history.const import DOMAIN


async def test_user_flow_then_single_instance(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"targets": [], "max_days": 30, "max_count": 200, "logbook": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {
        "targets": [],
        "max_days": 30,
        "max_count": 200,
        "logbook": True,
        "persistent_notifications": False,
    }
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, config_entry) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"targets": ["notify.x"], "max_days": 10, "max_count": 50, "logbook": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert config_entry.options["max_days"] == 10
    assert config_entry.options["persistent_notifications"] is False
