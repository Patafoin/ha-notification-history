"""normalize_call on real payloads (anonymised) from ha-server and the Companion docs."""
from datetime import UTC, datetime

from custom_components.notification_history.normalize import is_command, normalize_call, persistent_record

FRIGATE = {
    "title": "Mouvement detecte",
    "message": "Person - Interieur",
    "data": {
        "image": "https://ha.example.org/api/frigate/frigate/notifications/1700000000.1-abc123/thumbnail.jpg",
        "clickAction": "/media-browser/browser/app%2Cmedia-source%3A%2F%2Ffrigate",
        "tag": "1700000000.1-abc123",
        "group": "frigate-notifications",
    },
}


def call(service, data, ctx="01CTX"):
    return normalize_call(service, data, ts=1793725954.123456, context_id=ctx, user_id="u1")


def test_ios_frigate() -> None:
    records, removals = call("mobile_app_phone", FRIGATE)
    assert removals == []
    [r] = records
    assert r["id"] == "01CTX:notify.mobile_app_phone"
    assert r["target"] == "notify.mobile_app_phone"
    assert r["title"] == "Mouvement detecte"
    assert r["image"].endswith("/thumbnail.jpg")
    assert r["link"].startswith("/media-browser/")
    assert r["tag"] == "1700000000.1-abc123"
    assert r["status"] == "sent"
    assert r["ts"] == 1793725954.123456


def test_url_wins_over_click_action_and_actions_kept() -> None:
    [r], _ = call(
        "mobile_app_phone",
        {
            "message": "Clip",
            "data": {
                "url": "/lovelace/0",
                "clickAction": "/other",
                "actions": [
                    {"action": "URI", "title": "Voir le clip", "uri": "https://ha.example.org/clip.mp4"},
                    {"action": "ALARM_OFF", "title": "Désactiver"},
                    {"title": "no action key"},
                ],
            },
        },
    )
    assert r["link"] == "/lovelace/0"
    assert r["actions"] == [
        {"action": "URI", "title": "Voir le clip", "uri": "https://ha.example.org/clip.mp4"},
        {"action": "ALARM_OFF", "title": "Désactiver"},
    ]


def test_ios_attachment_overrides_media() -> None:
    [r], _ = call("mobile_app_phone", {"message": "m", "data": {"image": "/a.jpg", "attachment": {"url": "/clip.mp4"}}})
    assert r["image"] == "/a.jpg" and r["video"] == "/clip.mp4"
    [r], _ = call("mobile_app_phone", {"message": "m", "data": {"attachment": {"url": "/x?id=1", "content-type": "jpeg"}}})
    assert r["image"] == "/x?id=1"


def test_send_message_one_record_per_entity() -> None:
    records, _ = call("send_message", {"message": "Il fait 19 degrés", "entity_id": ["notify.echo", "notify.phone", "light.x"]})
    assert [r["target"] for r in records] == ["notify.echo", "notify.phone"]
    assert records[0]["title"] is None


def test_legacy_target_list_kept() -> None:
    [r], _ = call("alexa_media", {"message": "Bonjour", "target": ["media_player.salon"], "data": {"type": "announce"}})
    assert r["target"] == "notify.alexa_media"
    assert r["sub_targets"] == ["media_player.salon"]


def test_commands_are_not_notifications() -> None:
    assert is_command("command_webview") and is_command("kiosk_reload") and is_command("clear_badge")
    assert not is_command("Take the bins out")
    assert call("mobile_app_tablet", {"message": "command_webview", "data": {"command": "/dash"}}) == ([], [])
    assert call("mobile_app_phone", {"message": "request_location_update"}) == ([], [])


def test_clear_notification_with_tag_is_a_removal() -> None:
    records, removals = call("mobile_app_phone", {"message": "clear_notification", "data": {"tag": "t1"}})
    assert records == [] and removals == [("notify.mobile_app_phone", "t1")]


def test_empty_message_ignored() -> None:
    assert call("mobile_app_phone", {}) == ([], [])


def test_persistent_record() -> None:
    created = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)
    record = persistent_record(
        {"notification_id": "config_entry_discovery", "title": "New devices discovered", "message": "We have discovered new devices", "created_at": created},
        now=0,
    )
    assert record["target"] == "persistent_notification" and record["owner"] is None
    assert record["tag"] == "config_entry_discovery" and record["ts"] == created.timestamp()
    assert record["id"] == f"persistent_notification:config_entry_discovery:{created.timestamp():.6f}"
    assert record["title"] == "New devices discovered" and record["status"] == "sent"
    assert persistent_record({"notification_id": "x", "message": " ", "title": None}, now=0) is None
