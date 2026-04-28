from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import messages_service


def _headers() -> dict[str, str]:
    return {"X-API-Key": get_settings().stoic_api_key.get_secret_value()}


def test_health_is_open_and_database_is_available():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_data_endpoints_require_api_key():
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")

    assert response.status_code == 401


def test_read_only_endpoints_smoke():
    headers = _headers()
    cases = [
        ("/api/v1/meta", 200),
        ("/api/v1/entities", 200),
        ("/api/v1/entities?limit=101", 422),
        ("/api/v1/entities/unknown", 404),
        ("/api/v1/search?q=test", 200),
        ("/api/v1/incoming-sources", 200),
        ("/api/v1/incoming-sources/unknown/fragments", 404),
        ("/api/v1/review-queue", 200),
        ("/api/v1/tasks/deadlines/today", 200),
        ("/api/v1/tasks/deadlines/today/count", 200),
        ("/api/v1/tasks/backlog", 200),
        ("/api/v1/calendar/events/today", 200),
        ("/api/v1/calendar/events?date_from=2026-04-01&date_to=2026-04-30", 200),
        ("/api/v1/messages/conversations?channel=assistant", 200),
        ("/api/v1/messages/telegram/auth/status", 200),
    ]

    with TestClient(app) as client:
        for path, expected_status in cases:
            response = client.get(path, headers=headers)
            assert response.status_code == expected_status, response.text


def test_empty_collections_have_items_shape():
    headers = _headers()
    with TestClient(app) as client:
        entities = client.get("/api/v1/entities", headers=headers)
        incoming_sources = client.get("/api/v1/incoming-sources", headers=headers)
        review_queue = client.get("/api/v1/review-queue", headers=headers)

    assert entities.status_code == 200
    assert incoming_sources.status_code == 200
    assert review_queue.status_code == 200
    assert "items" in entities.json()
    assert "items" in incoming_sources.json()
    assert "items" in review_queue.json()


def test_task_deadline_count_supports_explicit_date():
    headers = _headers()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/tasks/deadlines/today/count?date=2026-05-10",
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-05-10"
    assert body["timezone"] == "Europe/Moscow"
    assert body["count"] >= 0


def test_task_deadline_list_returns_task_text_and_time():
    headers = _headers()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/tasks/deadlines/today?date=2026-05-10",
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-05-10"
    if body["items"]:
        assert {"text", "deadline_at", "time", "done"}.issubset(body["items"][0])
    else:
        assert body["items"] == []


def test_calendar_events_today_returns_time_slots():
    headers = _headers()
    with TestClient(app) as client:
        response = client.get("/api/v1/calendar/events/today", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == "Europe/Moscow"
    if body["items"]:
        assert {"title", "starts_at", "date", "time", "color"}.issubset(body["items"][0])
    else:
        assert body["items"] == []


def test_assistant_messages_are_saved_and_readable(monkeypatch):
    headers = _headers()

    async def fake_run_codex_chat(**_: object):
        return {
            "conversation_id": "codex-thread-test",
            "message": "Готово",
            "model": "gpt-5.2",
            "reasoning_effort": "medium",
            "sandbox": "read-only",
            "approval_policy": "on-request",
            "usage": None,
        }

    monkeypatch.setattr(messages_service, "run_codex_chat", fake_run_codex_chat)

    with TestClient(app) as client:
        create = client.post(
            "/api/v1/messages/assistant",
            headers=headers,
            json={
                "message": "Тестовое сообщение для истории",
                "sandbox": "read-only",
                "approval_policy": "on-request",
            },
        )

        assert create.status_code == 200, create.text
        body = create.json()
        conversation_id = body["conversation_id"]

        conversations = client.get("/api/v1/messages/conversations?channel=assistant&limit=5", headers=headers)
        history = client.get(
            f"/api/v1/messages/conversations/{conversation_id}/messages?limit=10",
            headers=headers,
        )

    assert conversations.status_code == 200
    assert history.status_code == 200
    conversation_ids = [item["id"] for item in conversations.json()["items"]]
    assert conversation_id in conversation_ids
    messages = history.json()["items"]
    assert len(messages) >= 2
    assert {item["sender_role"] for item in messages}.issuperset({"user", "assistant"})
