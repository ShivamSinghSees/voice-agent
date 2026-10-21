"""Unit tests for latency measurement, TrackingLLM timing markers, and latency reporting."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

import sys
from pathlib import Path

# Add scripts directory to path for import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from livekit.agents import llm
from latency_report import compute_stats, generate_report, parse_logs, percentile
from tracking_llm import TrackingLLM


def test_percentile_calculations():
    # Odd length: [10, 20, 30] -> median is 20
    data = [10.0, 20.0, 30.0]
    assert percentile(data, 50.0) == 20.0
    assert percentile(data, 0.0) == 10.0
    assert percentile(data, 100.0) == 30.0

    # 100 items: 1..100 -> p95 should be ~95.05
    data_100 = list(range(1, 101))
    p95 = percentile([float(x) for x in data_100], 95.0)
    assert 94.0 <= p95 <= 96.0


def test_compute_stats_preserves_negative_values():
    # Rule 3: Never clamp negative deltas; log raw
    neg_series = [-150.0, -50.0, 100.0, 200.0]
    stats = compute_stats(neg_series)
    assert stats["min"] == -150.0
    assert stats["max"] == 200.0
    assert stats["count"] == 4


def test_parse_logs_deduplication_and_greeting_exclusion():
    sample_logs = [
        "2026-10-21 10:00:00 [INFO] voice-worker: AgentSession args: allow_interruptions=True, min_interruption_duration=0.50, min_interruption_words=0, turn_detection=turn_detector.EOUModel, vad=Silero, min_endpointing_delay=0.50, max_endpointing_delay=3.00, preemptive_generation=True",
        # Greeting turn (turn_idx=0)
        '2026-10-21 10:00:01 [INFO] voice-worker: [LATENCY_GREETING] {"call_id": "call-1", "turn_idx": 0, "join_to_greeting_audio_ms": 1120.5, "started_speaking_at": 1700000001.0, "text": "Hello! How can I help?"}',
        # Duplicate greeting (should be deduped)
        '2026-10-21 10:00:01 [INFO] voice-worker: [LATENCY_GREETING] {"call_id": "call-1", "turn_idx": 0, "join_to_greeting_audio_ms": 1120.5, "started_speaking_at": 1700000001.0, "text": "Hello! How can I help?"}',
        # Conversational turn 1
        '2026-10-21 10:00:05 [INFO] voice-worker: [LATENCY] {"call_id": "call-1", "turn_idx": 1, "timestamps": {"user_stopped": 1700000005.0, "stt_final": 1700000005.3, "llm_first_token": 1700000005.6, "tts_first_audio": 1700000005.8, "audio_published": 1700000005.9}, "deltas_ms": {"vad_to_stt": 300.0, "stt_to_llm": 300.0, "llm_to_tts": 200.0, "tts_to_publish": 100.0}, "cumulative_ms": {"user_stopped": 0.0, "stt_final": 300.0, "llm_first_token": 600.0, "tts_first_audio": 800.0, "audio_published": 900.0}, "total_ms": 900.0, "flags": {"interrupted": false, "whisper_in_ctx": false, "preemptive_used": false}}',
        # Duplicate of conversational turn 1 (deduped by (call_id, turn_idx))
        '2026-10-21 10:00:05 [INFO] voice-worker: [LATENCY] {"call_id": "call-1", "turn_idx": 1, "timestamps": {"user_stopped": 1700000005.0, "stt_final": 1700000005.3, "llm_first_token": 1700000005.6, "tts_first_audio": 1700000005.8, "audio_published": 1700000005.9}, "deltas_ms": {"vad_to_stt": 300.0, "stt_to_llm": 300.0, "llm_to_tts": 200.0, "tts_to_publish": 100.0}, "cumulative_ms": {"user_stopped": 0.0, "stt_final": 300.0, "llm_first_token": 600.0, "tts_first_audio": 800.0, "audio_published": 900.0}, "total_ms": 900.0, "flags": {"interrupted": false, "whisper_in_ctx": false, "preemptive_used": false}}',
        # Conversational turn 2 with negative delta from preemptive generation
        '2026-10-21 10:00:10 [INFO] voice-worker: [LATENCY] {"call_id": "call-1", "turn_idx": 2, "timestamps": {"user_stopped": 1700000010.0, "stt_final": 1700000010.35, "llm_first_token": 1700000010.25, "tts_first_audio": 1700000010.45, "audio_published": 1700000010.55}, "deltas_ms": {"vad_to_stt": 350.0, "stt_to_llm": -100.0, "llm_to_tts": 200.0, "tts_to_publish": 100.0}, "cumulative_ms": {"user_stopped": 0.0, "stt_final": 350.0, "llm_first_token": 250.0, "tts_first_audio": 450.0, "audio_published": 550.0}, "total_ms": 550.0, "flags": {"interrupted": false, "whisper_in_ctx": true, "preemptive_used": true}}',
        # Interruption latency entry
        '2026-10-21 10:00:15 [INFO] voice-worker: [INTERRUPT_LATENCY] {"call_id": "call-1", "turn_idx": 2, "timestamps": {"user_speech_onset": 1700000015.0, "interruption_event": 1700000015.35, "playout_stop": 1700000015.42}, "deltas_ms": {"onset_to_event": 350.0, "event_to_stop": 70.0, "total_interruption_latency": 420.0}, "source": "user_turn"}',
    ]

    parsed = parse_logs(sample_logs)

    # Assert session args parsed correctly
    assert parsed["session_args"]["allow_interruptions"] == "True"
    assert parsed["session_args"]["min_interruption_duration"] == "0.50"
    assert parsed["session_args"]["min_endpointing_delay"] == "0.50"

    # Assert greeting deduplicated (only 1 greeting entry for call-1)
    assert len(parsed["greetings"]) == 1
    assert parsed["greetings"][0]["join_to_greeting_audio_ms"] == 1120.5

    # Assert turns deduplicated by (call_id, turn_idx): 2 unique turns (turn 1 and turn 2)
    assert len(parsed["turns"]) == 2
    turn1 = [t for t in parsed["turns"] if t["turn_idx"] == 1][0]
    turn2 = [t for t in parsed["turns"] if t["turn_idx"] == 2][0]

    assert turn1["deltas_ms"]["vad_to_stt"] == 300.0
    # Turn 2 raw negative delta preserved
    assert turn2["deltas_ms"]["stt_to_llm"] == -100.0
    assert turn2["flags"]["preemptive_used"] is True

    # Assert interruption parsed
    assert len(parsed["interruptions"]) == 1
    assert parsed["interruptions"][0]["deltas_ms"]["total_interruption_latency"] == 420.0

    # Generate report
    report = generate_report(parsed)
    assert "Headline: User Stopped -> Audio Published" in report
    assert "allow_interruptions: True" in report
    assert "preemptive_generation used: 1 turns" in report
    assert "-100.0" in report or "-100" in report


@pytest.mark.anyio
async def test_tracking_llm_timing():
    inner_llm = MagicMock()
    inner_llm.model = "test-model"

    chunk1 = MagicMock()
    chunk1.delta.content = "Hello "
    chunk2 = MagicMock()
    chunk2.delta.content = "world!"

    async def mock_generator():
        yield chunk1
        yield chunk2

    mock_stream = MagicMock()
    mock_stream.__aiter__.side_effect = lambda: mock_generator()
    mock_stream.aclose = AsyncMock()

    inner_llm.chat.return_value = mock_stream

    tracker = TrackingLLM(inner_llm)
    chat_ctx = MagicMock()
    stream = tracker.chat(chat_ctx=chat_ctx)

    assert tracker.latest_chat_called_at is not None
    assert tracker.latest_first_token_at is None

    # Run the stream to collect deltas
    await stream._run()

    assert tracker.latest_first_token_at is not None
    assert tracker.latest_first_token_at >= tracker.latest_chat_called_at
    assert tracker.latest_full_text == "Hello world!"


def test_conversation_item_failsafe_latency_does_not_block_transcript():
    """Verify that exceptions raised in the latency measurement block do not prevent transcript writes."""
    from agent import handle_conversation_item

    mock_write = MagicMock()
    mock_record = MagicMock(return_value="coro_placeholder")

    # Force a failure inside the latency measurement block (e.g. tracking_llm explodes)
    mock_tracker = MagicMock()
    type(mock_tracker).latest_full_text = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("Simulated latency block explosion"))
    )

    item = llm.ChatMessage(
        role="assistant",
        content=["To clarify, Acme Telecom provides communication services."],
        interrupted=True,
    )

    state = {
        "agent_turn_idx": 1,
        "last_user_turn_metrics": {"stopped_speaking_at": 100.0, "transcription_delay": 0.5},
        "last_user_turn_text": "What is your policy?",
    }

    # Execute handler - must NOT raise
    handle_conversation_item(
        item,
        call_id="call-4faf5922",
        transcript_state=state,
        schedule_transcript_write=mock_write,
        record_transcript_line=mock_record,
        tracking_llm=mock_tracker,
    )

    # Transcript line was recorded FIRST and INDEPENDENTLY
    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["speaker"] == "agent"
    assert mock_record.call_args.kwargs["text"] == "To clarify, Acme Telecom provides communication services."
    mock_write.assert_called_once_with("coro_placeholder")

    # State was still cleaned up
    assert state["last_user_turn_metrics"] == {}


def test_user_turn_failsafe_latency_does_not_block_transcript():
    """Verify that user turn transcript write succeeds even if metrics fail."""
    from agent import handle_conversation_item

    mock_write = MagicMock()
    mock_record = MagicMock(return_value="coro_placeholder")

    item = llm.ChatMessage(
        role="user",
        content=["I need help with my account."],
    )

    state = {
        "agent_turn_idx": 0,
        "last_user_turn_metrics": {},
        "last_user_turn_text": "",
    }

    handle_conversation_item(
        item,
        call_id="call-4faf5922",
        transcript_state=state,
        schedule_transcript_write=mock_write,
        record_transcript_line=mock_record,
    )

    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["speaker"] == "user"
    assert mock_record.call_args.kwargs["text"] == "I need help with my account."
    mock_write.assert_called_once_with("coro_placeholder")


def test_greeting_item_records_transcript_and_logs_latency_greeting(caplog):
    """Regression test: call handle_conversation_item with the greeting (assistant message, no preceding user turn, disconnect_fut not yet done/None) and assert a transcript line is recorded and LATENCY_GREETING is logged."""
    import asyncio
    from agent import handle_conversation_item

    mock_write = MagicMock()
    mock_record = MagicMock(return_value="coro_placeholder")

    greeting_text = (
        "Hello! Thank you for calling customer support. "
        "Please note that this call is recorded for quality assurance. "
        "How can I help you today?"
    )
    greeting_item = llm.ChatMessage(
        role="assistant",
        content=[greeting_text],
    )
    greeting_item.metrics = {
        "started_speaking_at": 1700000002.5,
        "stopped_speaking_at": 1700000007.8,
    }

    state = {
        "agent_turn_idx": 0,
        "last_user_turn_metrics": {},
        "last_user_turn_text": "",
    }

    loop = asyncio.new_event_loop()
    disconnect_fut = loop.create_future()

    with caplog.at_level("INFO"):
        handle_conversation_item(
            greeting_item,
            call_id="call-730e5cc4-97b6-4737-ba1d-e4c64966d4c5",
            transcript_state=state,
            schedule_transcript_write=mock_write,
            record_transcript_line=mock_record,
            disconnect_fut=disconnect_fut,
            customer_joined_at=1700000000.0,
            worker_saw_at=1700000000.2,
        )

    # 1. Assert transcript line is recorded
    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["speaker"] == "agent"
    assert mock_record.call_args.kwargs["text"] == greeting_text
    mock_write.assert_called_once_with("coro_placeholder")

    # 2. Assert [LATENCY_GREETING] is logged
    assert "[LATENCY_GREETING]" in caplog.text
    assert "call-730e5cc4" in caplog.text
    assert state["agent_turn_idx"] == 1
    assert state["last_user_turn_metrics"] == {}


def test_greeting_item_with_none_disconnect_fut(caplog):
    """Verify greeting item succeeds when disconnect_fut is None and no preceding user turn."""
    from agent import handle_conversation_item

    mock_write = MagicMock()
    mock_record = MagicMock(return_value="coro_placeholder")

    greeting_item = llm.ChatMessage(
        role="assistant",
        content=["Welcome to customer support."],
    )

    state = {
        "agent_turn_idx": 0,
        "last_user_turn_metrics": {},
    }

    with caplog.at_level("INFO"):
        handle_conversation_item(
            greeting_item,
            call_id="call-none-fut",
            transcript_state=state,
            schedule_transcript_write=mock_write,
            record_transcript_line=mock_record,
            disconnect_fut=None,
        )

    mock_record.assert_called_once()
    assert mock_record.call_args.kwargs["speaker"] == "agent"
    assert mock_record.call_args.kwargs["text"] == "Welcome to customer support."
    mock_write.assert_called_once_with("coro_placeholder")
    assert "[LATENCY_GREETING]" in caplog.text


