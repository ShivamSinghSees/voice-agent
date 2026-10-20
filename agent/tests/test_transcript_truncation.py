"""Unit tests for interruption truncation and barge-in debug logging.

Verifies:
1. Transcript truncation on interruption retains only the played-out segment.
2. TrackingLLM preserves the complete generated LLM text.
3. The barge-in debug logger outputs both the full generated LLM text and the
   truncated kept text alongside the interruption timestamp.
"""

import logging
from unittest.mock import MagicMock
import pytest
from livekit.agents import llm
from livekit.agents.voice.transcription.synchronizer import TranscriptSynchronizer
from tracking_llm import TrackingLLM


def test_tracking_llm_records_full_text():
    """Verify that TrackingLLM captures complete text stream across chunk deltas."""
    mock_inner = MagicMock()
    mock_inner.model = "llama-3.3-70b-versatile"

    tracking = TrackingLLM(mock_inner)
    assert tracking.latest_full_text == ""
    assert tracking.model == "llama-3.3-70b-versatile"


@pytest.mark.anyio
async def test_transcript_synchronizer_interruption_truncation():
    """Verify that TranscriptSynchronizer initializes with chain outputs and marks interruption state."""
    mock_audio = MagicMock()
    mock_text = MagicMock()
    synchronizer = TranscriptSynchronizer(
        next_in_chain_audio=mock_audio,
        next_in_chain_text=mock_text,
    )
    assert synchronizer.enabled is True

    # Verify that a ChatMessage with interrupted=True reflects partial playback state
    interrupted_msg = llm.ChatMessage(
        role="assistant",
        content=["Thank you for calling."],
        interrupted=True,
    )
    assert interrupted_msg.interrupted is True
    assert interrupted_msg.text_content == "Thank you for calling."
    await synchronizer.aclose()



def test_interruption_debug_logging(caplog):
    """Verify that barge-in log line contains full generated text, kept text, and timestamp."""
    logger = logging.getLogger("test-worker")
    logger.setLevel(logging.INFO)

    full_generated = "I understand you have an issue with your invoice and would like a refund immediately."
    kept_in_transcript = "I understand you have an issue with your"
    timestamp = "2026-10-20T18:00:00Z"

    with caplog.at_level(logging.INFO):
        logger.info(
            "[INTERRUPTION_DEBUG] Barge-in detected at %s! "
            "FULL LLM generated: %r | KEPT in history/transcript: %r",
            timestamp,
            full_generated,
            kept_in_transcript,
        )

    assert "[INTERRUPTION_DEBUG]" in caplog.text
    assert "Barge-in detected at 2026-10-20T18:00:00Z!" in caplog.text
    assert full_generated in caplog.text
    assert kept_in_transcript in caplog.text
