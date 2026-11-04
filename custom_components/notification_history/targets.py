"""Resolve a notify target (`notify.<service>` or a notify entity) to its device and owner."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import EXCLUDED_PLATFORMS, EXCLUDED_SERVICES

NOTIFY_SERVICES = "notify_services"  # hass.data key of notify/legacy.py (a HassKey, i.e. a str)


def _legacy_index(hass: HomeAssistant) -> dict[str, tuple[str, Any]]:
    """service name -> (platform, registered target value) for legacy notify services."""
    index: dict[str, tuple[str, Any]] = {}
    for platform, services in (hass.data.get(NOTIFY_SERVICES) or {}).items():
        for service in services:
            if name := getattr(service, "_service_name", None):
                index[name] = (platform, None)
            for target_name, value in (getattr(service, "registered_targets", None) or {}).items():
                index[target_name] = (platform, value)
    return index


def _mobile_app_entry(hass: HomeAssistant, *, webhook_id: str | None = None, entry_id: str | None = None):
    for entry in hass.config_entries.async_entries("mobile_app"):
        if entry.entry_id == entry_id or (webhook_id and entry.data.get("webhook_id") == webhook_id):
            return entry
    return None


def resolve(hass: HomeAssistant, target: str, index: dict[str, tuple[str, Any]] | None = None) -> dict[str, Any]:
    """{target, name, platform, device_id, owner, excluded} for a target."""
    index = _legacy_index(hass) if index is None else index
    service = target.removeprefix("notify.")
    info: dict[str, Any] = {"target": target, "name": None, "platform": None, "device_id": None, "owner": None}
    entry = None

    if service in index:
        info["platform"], value = index[service]
        if info["platform"] == "mobile_app" and isinstance(value, str):
            entry = _mobile_app_entry(hass, webhook_id=value)
    elif ent := er.async_get(hass).async_get(target):
        info["platform"] = ent.platform
        info["device_id"] = ent.device_id
        if ent.platform == "mobile_app" and ent.config_entry_id:
            entry = _mobile_app_entry(hass, entry_id=ent.config_entry_id)
        # The state's friendly name includes the device name ("Echo Dot Announce", not "Announce").
        state = hass.states.get(target)
        info["name"] = (state.name if state else None) or ent.name or ent.original_name

    if entry is not None:
        info["owner"] = entry.data.get("user_id")
        info["name"] = entry.title
        if info["device_id"] is None and (device_id := entry.data.get("device_id")):
            registry = dr.async_get(hass)
            if hasattr(registry, "async_get_device_by_identifier"):  # HA 2026.10+, async_get_device is deprecated
                device = registry.async_get_device_by_identifier(("mobile_app", device_id), entry.entry_id)
            else:
                device = registry.async_get_device(identifiers={("mobile_app", device_id)})
            info["device_id"] = device.id if device else None

    if info["name"] is None:
        state = hass.states.get(target)
        platform = info["platform"]
        info["name"] = (state.name if state else None) or (
            service if platform in (None, service) else f"{service} ({platform})"
        )
    info["excluded"] = service in EXCLUDED_SERVICES or info["platform"] in EXCLUDED_PLATFORMS
    return info


def available_targets(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every notify target that can be recorded (legacy services and notify entities)."""
    index = _legacy_index(hass)
    names = {f"notify.{s}" for s in hass.services.async_services_for_domain("notify") if s != "send_message"}
    names |= set(hass.states.async_entity_ids("notify"))
    out = [resolve(hass, t, index) for t in sorted(names)]
    return [t for t in out if not t["excluded"]]
