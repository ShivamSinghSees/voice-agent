"""Unit tests for Phase 6 post-call compliance checking.

Covers:
- Rule 1: Deterministic recording notice (raw total_seconds <= 30.0s, truncated greeting, sentence context regex, clamping).
- Rule 2: LLM refund promise check (valid pass, valid fail, retry on invalid JSON, offending text not in transcript, persistent invalid error).
- Plumbing & Idempotency: Single UPDATE WHERE status!='ended', duplicate end calls, legacy null compliance, and exception resilience.
"""

import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app import compliance, config, schemas, storage
from app.compliance import evaluate_recording_notice, evaluate_refund_promise, normalize_text
from app.main import app


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Isolate test DB for each test."""
    test_db = str(tmp_path / "test_compliance.db")
    config.DATABASE_PATH = test_db
    config.INTERNAL_API_TOKEN = "test-internal-token-secret"
    config.GROQ_API_KEY = "test-groq-key"
    storage.init_db(test_db)
    yield


@pytest.fixture
def client():
    return TestClient(app)


# --- Rule 1 Deterministic Unit Tests ---

def test_rule1_pass_at_4s():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    lines = [
        {
            "seq": 0,
            "speaker": "agent",
            "text": "Hello! Please note that this call is recorded for quality assurance.",
            "started_at": "2026-10-21T10:00:04.000000Z",
            "ended_at": "2026-10-21T10:00:08.000000Z",
        }
    ]
    res = evaluate_recording_notice(call, lines)
    assert res["passed"] is True
    assert res["evidence_seq"] == 0
    assert res["offset_seconds"] == 4.0
    assert "offset 4.0s" in res["reason"]


def test_rule1_pass_at_exactly_30s():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    lines = [
        {
            "seq": 0,
            "speaker": "agent",
            "text": "This call is being recorded.",
            "started_at": "2026-10-21T10:00:30.000000Z",
            "ended_at": "2026-10-21T10:00:34.000000Z",
        }
    ]
    res = evaluate_recording_notice(call, lines)
    assert res["passed"] is True
    assert res["evidence_seq"] == 0
    assert res["offset_seconds"] == 30.0


def test_rule1_fail_at_31s():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    lines = [
        {
            "seq": 0,
            "speaker": "agent",
            "text": "This call is being recorded.",
            "started_at": "2026-10-21T10:00:31.000000Z",
            "ended_at": "2026-10-21T10:00:35.000000Z",
        }
    ]
    res = evaluate_recording_notice(call, lines)
    assert res["passed"] is False
    assert res["evidence_seq"] == 0
    assert res["offset_seconds"] == 31.0
    assert "exceeding the 30.0s requirement" in res["reason"]


def test_rule1_fail_when_greeting_truncated_before_recorded():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    lines = [
        {
            "seq": 0,
            "speaker": "agent",
            "text": "Hello! Thank you for calling customer support. Please note that this",
            "started_at": "2026-10-21T10:00:02.000000Z",
            "ended_at": "2026-10-21T10:00:04.000000Z",
        }
    ]
    res = evaluate_recording_notice(call, lines)
    assert res["passed"] is False
    assert res["evidence_seq"] is None
    assert "No recording disclosure found" in res["reason"]


def test_rule1_fail_with_no_agent_lines():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    lines = [
        {
            "seq": 0,
            "speaker": "user",
            "text": "Hello, is this call recorded?",
            "started_at": "2026-10-21T10:00:02.000000Z",
            "ended_at": "2026-10-21T10:00:04.000000Z",
        }
    ]
    res = evaluate_recording_notice(call, lines)
    assert res["passed"] is False
    assert res["evidence_seq"] is None
    assert "No agent transcript lines found" in res["reason"]


def test_rule1_regex_negative_sentence_context():
    call = {"started_at": "2026-10-21T10:00:00.000000Z"}
    # 1. World record (record without call/conversation)
    lines1 = [{
        "seq": 0,
        "speaker": "agent",
        "text": "I hold the world record in customer service speed.",
        "started_at": "2026-10-21T10:00:02.000000Z",
        "ended_at": "2026-10-21T10:00:05.000000Z",
    }]
    assert evaluate_recording_notice(call, lines1)["passed"] is False

    # 2. Record note without call context
    lines2 = [{
        "seq": 0,
        "speaker": "agent",
        "text": "Let me record your account number in my notepad.",
        "started_at": "2026-10-21T10:00:02.000000Z",
        "ended_at": "2026-10-21T10:00:05.000000Z",
    }]
    assert evaluate_recording_notice(call, lines2)["passed"] is False

    # 3. Call and record across separate sentences
    lines3 = [{
        "seq": 0,
        "speaker": "agent",
        "text": "Thank you for the phone call. I will record your ticket details now.",
        "started_at": "2026-10-21T10:00:02.000000Z",
        "ended_at": "2026-10-21T10:00:05.000000Z",
    }]
    assert evaluate_recording_notice(call, lines3)["passed"] is False


def test_agent_ended_at_clamped_to_call_ended_at():
    call_id = "test-clamp-call"
    agent_id = "test-agent"
    storage.create_agent(
        config.DATABASE_PATH,
        agent_id=agent_id,
        name="Support",
        system_prompt="Help",
        voice="voice",
        model="model",
        created_at="2026-10-21T10:00:00Z",
    )
    storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name="room-clamp",
        created_at="2026-10-21T10:00:00Z",
    )
    storage.set_call_started(config.DATABASE_PATH, call_id, "2026-10-21T10:00:05.000000Z")
    storage.transition_call_to_ended(config.DATABASE_PATH, call_id, "2026-10-21T10:01:00.000000Z")

    # Insert agent line with ended_at 5 seconds AFTER call ended
    storage.upsert_transcript_lines(
        config.DATABASE_PATH,
        call_id,
        [
            {
                "seq": 0,
                "speaker": "agent",
                "text": "Goodbye!",
                "started_at": "2026-10-21T10:00:55.000000Z",
                "ended_at": "2026-10-21T10:01:05.000000Z",
            }
        ],
    )

    # Calling get_transcript_lines with clamp=True (default) clamps ended_at
    lines = storage.get_transcript_lines(config.DATABASE_PATH, call_id, clamp_to_call_ended=True)
    assert lines[0]["ended_at"] == "2026-10-21T10:01:00.000000Z"

    # Calling clamp_agent_transcript_lines updates SQLite storage directly
    storage.clamp_agent_transcript_lines(config.DATABASE_PATH, call_id, "2026-10-21T10:01:00.000000Z")
    raw_lines = storage.get_transcript_lines(config.DATABASE_PATH, call_id, clamp_to_call_ended=False)
    assert raw_lines[0]["ended_at"] == "2026-10-21T10:01:00.000000Z"


# --- Rule 2 LLM Unit Tests (Mocked Groq) ---

@pytest.mark.anyio
async def test_rule2_valid_pass():
    transcript = [
        {"seq": 0, "speaker": "agent", "text": "Hello, how can I help?"},
        {"seq": 1, "speaker": "user", "text": "Can I get a refund?"},
        {"seq": 2, "speaker": "agent", "text": "I can't issue refunds under our company policy."},
    ]

    mock_llm_json = json.dumps({
        "passed": True,
        "offending_seq": None,
        "offending_text": None,
        "reason": "Agent explicitly refused refund and stated policy.",
    })

    mock_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": mock_llm_json}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        res = await evaluate_refund_promise(transcript)
        assert res["status"] == "done"
        assert res["passed"] is True
        assert res["offending_seq"] is None
        assert res["offending_text"] is None


@pytest.mark.anyio
async def test_rule2_valid_fail():
    transcript = [
        {"seq": 0, "speaker": "agent", "text": "Hello!"},
        {"seq": 1, "speaker": "user", "text": "My item arrived damaged."},
        {"seq": 2, "speaker": "agent", "text": "I apologize for that, I'll refund you right away."},
    ]

    mock_llm_json = json.dumps({
        "passed": False,
        "offending_seq": 2,
        "offending_text": "I'll refund you",
        "reason": "Agent promised to refund the customer.",
    })

    mock_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": f"<think>Reasoning...</think>{mock_llm_json}"}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        res = await evaluate_refund_promise(transcript)
        assert res["status"] == "done"
        assert res["passed"] is False
        assert res["offending_seq"] == 2
        assert res["offending_text"] == "I'll refund you"


@pytest.mark.anyio
async def test_rule2_retry_on_invalid_json_then_valid():
    transcript = [
        {"seq": 0, "speaker": "agent", "text": "Hello!"},
        {"seq": 1, "speaker": "agent", "text": "We cannot offer monetary compensation."},
    ]

    invalid_resp = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "Here is my evaluation: not valid json"}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )
    valid_resp = httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps({
            "passed": True,
            "offending_seq": None,
            "offending_text": None,
            "reason": "No refund promised.",
        })}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[invalid_resp, valid_resp]) as mock_post:
        res = await evaluate_refund_promise(transcript)
        assert res["status"] == "done"
        assert res["passed"] is True
        assert mock_post.call_count == 2


@pytest.mark.anyio
async def test_rule2_offending_text_not_in_transcript_retries_then_error():
    transcript = [
        {"seq": 0, "speaker": "agent", "text": "Hello!"},
        {"seq": 1, "speaker": "agent", "text": "I can help with billing."},
    ]

    # Model hallucinates text not present in line seq=1
    hallucinated_resp = httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps({
            "passed": False,
            "offending_seq": 1,
            "offending_text": "I will give you your money back immediately",
            "reason": "Agent promised refund",
        })}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=hallucinated_resp) as mock_post:
        res = await evaluate_refund_promise(transcript)
        assert res["status"] == "error"
        assert res["passed"] is None
        assert "is not a substring" in res["reason"]
        assert mock_post.call_count == 3


@pytest.mark.anyio
async def test_rule2_persistent_invalid_stores_error():
    transcript = [
        {"seq": 0, "speaker": "agent", "text": "Hello!"},
    ]

    bad_resp = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "{bad json"}}]},
        request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=bad_resp) as mock_post:
        res = await evaluate_refund_promise(transcript)
        assert res["status"] == "error"
        assert res["passed"] is None
        assert mock_post.call_count == 3


# --- Plumbing & Idempotency Tests ---

def test_transition_call_to_ended_atomic_idempotency():
    call_id = "test-atomic-call"
    agent_id = "test-agent"
    storage.create_agent(
        config.DATABASE_PATH,
        agent_id=agent_id,
        name="Support",
        system_prompt="Help",
        voice="voice",
        model="model",
        created_at="2026-10-21T10:00:00Z",
    )
    storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name="room-atomic",
        created_at="2026-10-21T10:00:00Z",
    )

    # First transition: status != 'ended' -> was_transitioned is True
    call, transitioned = storage.transition_call_to_ended(
        config.DATABASE_PATH, call_id, "2026-10-21T10:05:00Z", initial_compliance_json='{"status":"pending"}'
    )
    assert transitioned is True
    assert call["status"] == "ended"

    # Second transition: status is already 'ended' -> was_transitioned is False
    call2, transitioned2 = storage.transition_call_to_ended(
        config.DATABASE_PATH, call_id, "2026-10-21T10:06:00Z", initial_compliance_json='{"status":"pending"}'
    )
    assert transitioned2 is False
    assert call2["status"] == "ended"


def test_get_call_before_end_returns_null_compliance(client):
    agent_id = "agent-pre-end"
    storage.create_agent(
        config.DATABASE_PATH,
        agent_id=agent_id,
        name="Support",
        system_prompt="Help",
        voice="voice",
        model="model",
        created_at="2026-10-21T10:00:00Z",
    )
    call_id = "call-pre-end"
    storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name="room-pre-end",
        created_at="2026-10-21T10:00:00Z",
    )

    resp = client.get(f"/calls/{call_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["compliance"] is None


def test_get_call_old_ended_call_with_null_compliance_returns_200(client):
    agent_id = "agent-old"
    storage.create_agent(
        config.DATABASE_PATH,
        agent_id=agent_id,
        name="Support",
        system_prompt="Help",
        voice="voice",
        model="model",
        created_at="2026-10-21T10:00:00Z",
    )
    call_id = "call-old"
    storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name="room-old",
        created_at="2026-10-21T10:00:00Z",
    )
    # Ended call without any compliance JSON (compliance is NULL in DB)
    storage.transition_call_to_ended(config.DATABASE_PATH, call_id, "2026-10-21T10:05:00Z", initial_compliance_json=None)

    resp = client.get(f"/calls/{call_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ended"
    assert data["compliance"] is None


def test_llm_exception_does_not_break_get_call(client):
    agent_id = "agent-err"
    storage.create_agent(
        config.DATABASE_PATH,
        agent_id=agent_id,
        name="Support",
        system_prompt="Help",
        voice="voice",
        model="model",
        created_at="2026-10-21T10:00:00Z",
    )
    call_id = "call-err"
    storage.create_call(
        config.DATABASE_PATH,
        call_id=call_id,
        agent_id=agent_id,
        room_name="room-err",
        created_at="2026-10-21T10:00:00Z",
    )
    storage.set_call_started(config.DATABASE_PATH, call_id, "2026-10-21T10:00:00Z")
    storage.transition_call_to_ended(config.DATABASE_PATH, call_id, "2026-10-21T10:01:00Z")

    # Record error payload in DB
    storage.update_call_compliance(
        config.DATABASE_PATH,
        call_id,
        {
            "status": "error",
            "recording_notice": {
                "passed": True,
                "evidence_seq": 0,
                "offset_seconds": 3.5,
                "reason": "Recording disclosure stated at offset 3.5s",
            },
            "refund_promise": {
                "status": "error",
                "passed": None,
                "offending_seq": None,
                "offending_text": None,
                "reason": "Groq network timeout",
            },
            "checked_at": "2026-10-21T10:01:02Z",
        },
    )

    resp = client.get(f"/calls/{call_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["compliance"]["status"] == "error"
    assert data["compliance"]["recording_notice"]["passed"] is True
    assert data["compliance"]["refund_promise"]["status"] == "error"
