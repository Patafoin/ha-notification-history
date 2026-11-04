"""HistoryStore: replacement by tag, de-duplication, purge."""
from homeassistant.core import HomeAssistant

from custom_components.notification_history.store import HistoryStore


def rec(i, ts, target="notify.a", tag=None):
    return {"id": f"c{i}:{target}", "ts": ts, "target": target, "tag": tag, "status": "sent"}


async def test_tag_replaces_previous_and_ids_are_unique(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, 90, 500)
    store.add(rec(1, 100, tag="frigo"))
    store.add(rec(2, 110, target="notify.b", tag="frigo"))
    changed = store.add(rec(3, 120, tag="frigo"))
    assert [r["id"] for r in changed] == ["c3:notify.a", "c1:notify.a"]
    assert store.get("c1:notify.a")["status"] == "replaced"
    assert store.get("c2:notify.b")["status"] == "sent"  # other target
    assert store.add(rec(3, 120, tag="frigo")) == []


async def test_out_of_order_add_keeps_sorted(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, 90, 500)
    store.add(rec(2, 200))
    store.add(rec(1, 100))
    assert [r["ts"] for r in store.records] == [100, 200]


async def test_purge_by_age_then_count(hass: HomeAssistant) -> None:
    store = HistoryStore(hass, 1, 2)
    now = 1_000_000.0
    for i, ts in enumerate([now - 2 * 86400, now - 30, now - 20, now - 10]):
        store.add(rec(i, ts))
    assert store.purge(now) is True
    assert [r["id"] for r in store.records] == ["c2:notify.a", "c3:notify.a"]
    assert store.get("c0:notify.a") is None
    assert store.purge(now) is False


async def test_save_and_load(hass: HomeAssistant, hass_storage) -> None:
    store = HistoryStore(hass, 90, 500)
    store.add(rec(1, 100))
    store.backfilled = True
    await store.async_save()
    other = HistoryStore(hass, 90, 500)
    await other.async_load()
    assert other.backfilled and other.get("c1:notify.a")["ts"] == 100
