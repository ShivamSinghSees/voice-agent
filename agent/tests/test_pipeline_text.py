"""Integration test for Groq LLM pipeline and TrackingLLM text collection.

Exercises real Groq API invocation using GROQ_API_KEY from .env
to verify that instructions are honored and TrackingLLM captures full output.
"""

import pytest
from livekit.agents import llm
from livekit.plugins import groq
import config
from tracking_llm import TrackingLLM


@pytest.mark.anyio
async def test_groq_llm_text_pipeline():
    """Verify that real Groq LLM responds to prompts and TrackingLLM records full text."""
    if not config.GROQ_API_KEY:
        pytest.skip("GROQ_API_KEY not configured; skipping live Groq test")

    base_llm = groq.LLM(
        model="qwen/qwen3.8-27b",
        api_key=config.GROQ_API_KEY,
    )
    tracking = TrackingLLM(base_llm)

    system_prompt = (
        "You are an Acme Telecom support agent. "
        "Always begin by stating that calls are recorded for quality assurance."
    )
    chat_ctx = llm.ChatContext()
    chat_ctx.add_message(role="system", content=system_prompt)
    chat_ctx.add_message(role="user", content="Hello, can you help me check my bill?")

    stream = tracking.chat(chat_ctx=chat_ctx)
    chunks = []
    async for chunk in stream:
        if chunk.delta and chunk.delta.content:
            chunks.append(chunk.delta.content)

    full_output = "".join(chunks)
    assert len(full_output) > 0
    assert tracking.latest_full_text == full_output
    # Check that the system instruction was respected
    assert "record" in full_output.lower() or "quality" in full_output.lower()
