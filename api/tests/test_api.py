"""Unit tests for FastAPI application using FastAPI TestClient (sync) and temporary SQLite DB.

Mocks LiveKit interactions to isolate backend logic, permission gates, and idempotency.
"""

import json
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from app import config, storage
from app.livekit_service import LiveKitServiceError
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Point configuration to an isolated temporary SQLite database for each test."""
    test_db = str(tmp_path / "test_voice_agent.db")
    config.DATABASE_PATH = test_db
    config.INTERNAL_API_TOKEN = "test-internal-token-secret"
    storage.init_db(test_db)
    yield
    # Temp dir cleaned up automatically by pytest


@pytest.fixture
def client():
    """Create synchronous FastAPI TestClient."""
    return TestClient(app)


def test_agent_crud(client):
    """Test agent creation, listing, retrieval, and 404 behavior."""
    # 1. Create agent
    payload = {
        "name": "Acme Billing Support",
        "system_prompt": "You assist with billing inquiries.",
        "voice": "aura-asteria-en",
        "model": "llama-3.3-70b-versatile",
    }
    resp = client.post("/agents", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    agent_id = data["id"]
    assert data["name"] == "Acme Billing Support"
    assert "created_at" in data

    # 2. List agents
    resp = client.get("/agents")
    assert resp.status_code == 200
    agents = resp.json()
    assert any(a["id"] == agent_id for a in agents)

    # 3. Get single agent
    resp = client.get(f"/agents/{agent_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == agent_id

    # 4. Get non-existent agent returns 404
    resp = client.get("/agents/non-existent-agent-id")
    assert resp.status_code == 404


def test_call_creation_invalid_agent(client):
    """Creating a call with a non-existent agent_id must return 404."""
    resp = client.post("/calls", json={"agent_id": "missing-agent"})
    assert resp.status_code == 404
    assert "does not exist" in resp.json()["detail"]


@patch("app.main.create_room_and_dispatch", new_callable=AsyncMock)
def test_call_lifecycle_and_transcript_gate(mock_livekit, client):
    """Test full call flow: creation, token verification, internal worker notifications,

    transcript upsert idempotency, and transcript privacy gate until ended.
    """
    mock_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.mock_customer_jwt_token"
    mock_livekit.return_value = mock_token

    # 1. Setup an agent
    agent_resp = client.post(
        "/agents",
        json={
            "name": "Support Tier 1",
            "system_prompt": "Helpful tier 1 agent.",
            "voice": "aura-asteria-en",
            "model": "llama-3.3-70b-versatile",
        },
    )
    agent_id = agent_resp.json()["id"]

    # 2. POST /calls to start call
    call_start_resp = client.post("/calls", json={"agent_id": agent_id})
    assert call_start_resp.status_code == 201
    call_data = call_start_resp.json()
    call_id = call_data["call_id"]
    assert call_data["token"] == mock_token
    assert call_data["room_name"].startswith("call-")

    mock_livekit.assert_awaited_once_with(
        room_name=call_data["room_name"],
        call_id=call_id,
        agent_id=agent_id,
    )

    # 3. Verify GET /calls/{id} status is 'created' and transcript is NULL
    status_resp = client.get(f"/calls/{call_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "created"
    assert status_data["transcript"] is None

    # 4. Test Internal Auth: Missing / Invalid token must return 401
    unauth_resp = client.post(
        f"/internal/calls/{call_id}/started",
        json={"started_at": "2026-10-20T10:00:00Z"},
    )
    assert unauth_resp.status_code == 401

    wrong_token_resp = client.post(
        f"/internal/calls/{call_id}/started",
        headers={"X-Internal-Token": "wrong-secret-token"},
        json={"started_at": "2026-10-20T10:00:00Z"},
    )
    assert wrong_token_resp.status_code == 401

    # 5. Worker marks call started with valid token
    headers = {"X-Internal-Token": config.INTERNAL_API_TOKEN}
    started_resp = client.post(
        f"/internal/calls/{call_id}/started",
        headers=headers,
        json={"started_at": "2026-10-20T10:00:00Z"},
    )
    assert started_resp.status_code == 200
    assert started_resp.json()["status"] == "ok"

    # Re-verify GET /calls/{id}: status is 'active', transcript is STILL NULL
    active_resp = client.get(f"/calls/{call_id}")
    assert active_resp.json()["status"] == "active"
    assert active_resp.json()["transcript"] is None

    # 6. Worker upserts transcript lines
    lines_batch_1 = {
        "lines": [
            {
                "seq": 0,
                "speaker": "agent",
                "text": "Hello, thank you for calling Acme Telecom.",
                "started_at": "2026-10-20T10:00:01Z",
                "ended_at": "2026-10-20T10:00:04Z",
            },
            {
                "seq": 1,
                "speaker": "user",
                "text": "Hi, I have a question about my monthly bill.",
                "started_at": "2026-10-20T10:00:05Z",
                "ended_at": "2026-10-20T10:00:08Z",
            },
        ]
    }
    tx_resp = client.post(
        f"/internal/calls/{call_id}/transcript",
        headers=headers,
        json=lines_batch_1,
    )
    assert tx_resp.status_code == 200
    assert tx_resp.json()["count"] == 2

    # While still active, transcript must remain hidden from GET /calls/{id}
    assert client.get(f"/calls/{call_id}").json()["transcript"] is None

    # 7. Test Idempotent Upsert: Submitting identical or updated seq replaces, not duplicates
    updated_lines_batch = {
        "lines": [
            {
                "seq": 1,
                "speaker": "user",
                "text": "Hi, I have a quick question about my monthly bill.",
                "started_at": "2026-10-20T10:00:05Z",
                "ended_at": "2026-10-20T10:00:09Z",
            },
            {
                "seq": 2,
                "speaker": "agent",
                "text": "I can certainly help you review your bill.",
                "started_at": "2026-10-20T10:00:10Z",
                "ended_at": "2026-10-20T10:00:13Z",
            },
        ]
    }
    tx_resp2 = client.post(
        f"/internal/calls/{call_id}/transcript",
        headers=headers,
        json=updated_lines_batch,
    )
    assert tx_resp2.status_code == 200

    # 8. Worker signals call ended
    end_resp = client.post(
        f"/internal/calls/{call_id}/ended",
        headers=headers,
        json={"ended_at": "2026-10-20T10:00:15Z"},
    )
    assert end_resp.status_code == 200

    # 9. GET /calls/{id} now reveals the complete ordered transcript (3 rows total)
    ended_call_resp = client.get(f"/calls/{call_id}")
    assert ended_call_resp.status_code == 200
    ended_data = ended_call_resp.json()
    assert ended_data["status"] == "ended"
    assert ended_data["transcript"] is not None
    assert len(ended_data["transcript"]) == 3
    # Check sequences and updated text
    assert ended_data["transcript"][0]["seq"] == 0
    assert ended_data["transcript"][1]["seq"] == 1
    assert ended_data["transcript"][1]["text"] == "Hi, I have a quick question about my monthly bill."
    assert ended_data["transcript"][2]["seq"] == 2


@patch("app.main.create_room_and_dispatch", new_callable=AsyncMock)
def test_livekit_failure_returns_502_and_marks_call_failed(mock_livekit, client):
    """When LiveKit room/dispatch creation fails, API must return 502 Bad Gateway

    and record the call status as 'failed' in SQLite.
    """
    mock_livekit.side_effect = LiveKitServiceError("LiveKit connection timeout")

    # 1. Create an agent
    agent = client.post(
        "/agents",
        json={
            "name": "Acme Support",
            "system_prompt": "Support",
            "voice": "aura-asteria-en",
            "model": "llama-3.3-70b-versatile",
        },
    ).json()

    # 2. POST /calls triggers LiveKit error
    resp = client.post("/calls", json={"agent_id": agent["id"]})
    assert resp.status_code == 502
    assert "LiveKit server error" in resp.json()["detail"] or "Failed to provision" in resp.json()["detail"]

    # 3. Verify in DB that call is recorded with status 'failed'
    calls = storage.list_calls(config.DATABASE_PATH)
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"


@pytest.mark.anyio
async def test_create_room_and_dispatch_deduplication():
    """Verify create_room_and_dispatch sets CreateRoomRequest.agents atomically

    with agent_name="support-agent", preventing duplicate dispatch registrations.
    """
    from unittest.mock import MagicMock
    from app.livekit_service import create_room_and_dispatch

    mock_lkapi = MagicMock()
    mock_lkapi.room.create_room = AsyncMock()
    mock_lkapi.aclose = AsyncMock()

    with patch("app.livekit_service.get_livekit_api", return_value=mock_lkapi):
        token = await create_room_and_dispatch("test-room", "c1", "a1")
        assert token is not None

        # Verify CreateRoomRequest has agents populated atomically
        call_args = mock_lkapi.room.create_room.call_args[0][0]
        assert call_args.name == "test-room"
        assert call_args.empty_timeout == 60
        assert len(call_args.agents) == 1
        assert call_args.agents[0].agent_name == "support-agent"
        assert json.loads(call_args.agents[0].metadata) == {"call_id": "c1", "agent_id": "a1"}


def _seed_call(call_id: str) -> dict:
    """Insert an agent + call directly via storage (calls.agent_id has a FOREIGN KEY to agents.id)."""
    agent_id = f"agent-for-{call_id}"
    storage.create_agent(
        db_path=config.DATABASE_PATH,
        agent_id=agent_id,
        name="Seed Agent",
        system_prompt="Test prompt",
        voice="aura-asteria-en",
        model="qwen/qwen3.8-27b",
        created_at="2026-10-20T09:58:00Z",
    )
    return storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name=f"room-{call_id}",
        created_at="2026-10-20T09:59:00Z",
    )


def test_internal_endpoints_require_token(client):
    """Verify that all internal endpoints return HTTP 401 on missing or incorrect X-Internal-Token."""
    # Seed a call directly in DB
    call = _seed_call("test-auth-call")

    # 1. POST /internal/calls/{id}/started without token -> 401
    resp = client.post(f"/internal/calls/{call['id']}/started", json={"started_at": "2026-10-20T10:00:00Z"})
    assert resp.status_code == 401
    assert resp.json()["detail"].startswith("Unauthorized")

    # 2. POST /internal/calls/{id}/started with invalid token -> 401
    resp = client.post(
        f"/internal/calls/{call['id']}/started",
        headers={"X-Internal-Token": "bad-token-value"},
        json={"started_at": "2026-10-20T10:00:00Z"},
    )
    assert resp.status_code == 401

    # 3. POST /internal/calls/{id}/transcript with invalid token -> 401
    resp = client.post(
        f"/internal/calls/{call['id']}/transcript",
        headers={"X-Internal-Token": "bad-token-value"},
        json={"lines": []},
    )
    assert resp.status_code == 401

    # 4. POST /internal/calls/{id}/ended with invalid token -> 401
    resp = client.post(
        f"/internal/calls/{call['id']}/ended",
        headers={"X-Internal-Token": "bad-token-value"},
        json={"ended_at": "2026-10-20T10:05:00Z"},
    )
    assert resp.status_code == 401


def test_transcript_upsert_idempotency(client):
    """Verify that posting transcript lines with identical seq updates existing rows rather than duplicating."""
    call = _seed_call("test-idem-call")
    headers = {"X-Internal-Token": config.INTERNAL_API_TOKEN}

    # First upsert: seq 0
    resp1 = client.post(
        f"/internal/calls/{call['id']}/transcript",
        headers=headers,
        json={"lines": [{"seq": 0, "speaker": "agent", "text": "Original greeting", "started_at": "2026-10-20T10:00:00Z", "ended_at": "2026-10-20T10:00:02Z"}]},
    )
    assert resp1.status_code == 200

    # Second upsert: seq 0 with corrected text (barge-in truncation or correction)
    resp2 = client.post(
        f"/internal/calls/{call['id']}/transcript",
        headers=headers,
        json={"lines": [{"seq": 0, "speaker": "agent", "text": "Corrected greeting text", "started_at": "2026-10-20T10:00:00Z", "ended_at": "2026-10-20T10:00:03Z"}]},
    )
    assert resp2.status_code == 200

    # End call and verify exactly 1 transcript line exists with updated text
    client.post(f"/internal/calls/{call['id']}/ended", headers=headers, json={"ended_at": "2026-10-20T10:01:00Z"})
    call_resp = client.get(f"/calls/{call['id']}")
    assert call_resp.status_code == 200
    tx = call_resp.json()["transcript"]
    assert len(tx) == 1
    assert tx[0]["seq"] == 0
    assert tx[0]["text"] == "Corrected greeting text"


def test_transcript_gated_until_call_ended(client):
    """Verify that GET /calls/{id} transcript field remains null during 'created' and 'active' states,

    and is only revealed once the call transitions to 'ended'.
    """
    call = _seed_call("test-gate-call")
    headers = {"X-Internal-Token": config.INTERNAL_API_TOKEN}

    # 1. In 'created' state -> transcript must be None
    assert client.get(f"/calls/{call['id']}").json()["transcript"] is None

    # 2. Worker marks started -> status 'active' -> transcript must STILL be None
    client.post(f"/internal/calls/{call['id']}/started", headers=headers, json={"started_at": "2026-10-20T10:00:00Z"})
    assert client.get(f"/calls/{call['id']}").json()["status"] == "active"
    assert client.get(f"/calls/{call['id']}").json()["transcript"] is None

    # 3. Worker streams transcript rows while call is active -> transcript must STILL be None
    client.post(
        f"/internal/calls/{call['id']}/transcript",
        headers=headers,
        json={"lines": [{"seq": 0, "speaker": "agent", "text": "In progress speech", "started_at": "2026-10-20T10:00:01Z", "ended_at": "2026-10-20T10:00:04Z"}]},
    )
    assert client.get(f"/calls/{call['id']}").json()["transcript"] is None

    # 4. Worker marks call ended -> transcript is finally un-gated
    client.post(f"/internal/calls/{call['id']}/ended", headers=headers, json={"ended_at": "2026-10-20T10:00:10Z"})
    ended_call = client.get(f"/calls/{call['id']}").json()
    assert ended_call["status"] == "ended"
    assert ended_call["transcript"] is not None
    assert len(ended_call["transcript"]) == 1
    assert ended_call["transcript"][0]["text"] == "In progress speech"


def test_operator_token_generation_and_claims(client):
    """Test POST /calls/{id}/operator-token returns valid JWT with hidden grants."""
    from livekit import api as lk_api
    call = _seed_call("test-op-call")

    resp = client.post(f"/calls/{call['id']}/operator-token")
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_id"] == call["id"]
    assert data["room_name"] == call["room_name"]
    assert "token" in data
    assert "livekit_url" in data

    # Verify token claims
    verifier = lk_api.TokenVerifier(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
    claims = verifier.verify(data["token"])
    assert claims.identity.startswith("operator-")
    assert claims.video.hidden is True
    assert claims.video.can_subscribe is True
    assert claims.video.can_publish is False
    assert claims.video.can_publish_data is True
    assert claims.video.room == call["room_name"]


def test_operator_token_error_cases(client):
    """Test 404 for missing call and 409 for ended/failed call."""
    # 1. Unknown call -> 404
    resp = client.post("/calls/non-existent-call-id/operator-token")
    assert resp.status_code == 404

    # 2. Ended call -> 409
    call = _seed_call("test-ended-call")
    headers = {"X-Internal-Token": config.INTERNAL_API_TOKEN}
    client.post(f"/internal/calls/{call['id']}/ended", headers=headers, json={"ended_at": "2026-10-20T10:00:10Z"})
    resp = client.post(f"/calls/{call['id']}/operator-token")
    assert resp.status_code == 409
    assert "Cannot monitor call" in resp.json()["detail"]


def test_list_calls_filter_status(client):
    """Test GET /calls?status=active returns only active calls and includes agent_name."""
    c1 = _seed_call("test-filter-c1")
    c2 = _seed_call("test-filter-c2")
    headers = {"X-Internal-Token": config.INTERNAL_API_TOKEN}
    # Transition c1 to active
    client.post(f"/internal/calls/{c1['id']}/started", headers=headers, json={"started_at": "2026-10-20T10:00:00Z"})

    # Fetch active calls
    resp = client.get("/calls?status=active")
    assert resp.status_code == 200
    active_calls = resp.json()
    assert any(c["id"] == c1["id"] for c in active_calls)
    assert not any(c["id"] == c2["id"] for c in active_calls)
    active_call = next(c for c in active_calls if c["id"] == c1["id"])
    assert active_call["status"] == "active"
    assert active_call["agent_name"] == "Seed Agent"



