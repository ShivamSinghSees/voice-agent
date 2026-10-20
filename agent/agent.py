"""LiveKit Agents voice worker service.

Registers with the self-hosted LiveKit SFU as 'support-agent' to accept explicit
job dispatches from the FastAPI backend. Manages the real-time STT -> LLM -> TTS pipeline
with Silero VAD, barge-in interruption handling, customer participant session isolation,
and transcript synchronization with the backend.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any

from livekit import rtc
from livekit.agents import AgentServer, JobContext, cli, llm
from livekit.agents.voice import Agent, AgentSession, room_io
from livekit.plugins import deepgram, groq, silero

from api_client import APIClient, APIClientError
import config
from tracking_llm import TrackingLLM

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("voice-worker")

# Initialize AgentServer worker pool with default process-per-job executor
server = AgentServer()
api_client = APIClient()


def _format_iso(dt: datetime | None) -> str:
    """Format datetime as UTC ISO-8601 with millisecond precision, e.g. 2026-10-20T16:14:49.628Z.

    WHY milliseconds: transcript offsets and join-vs-detection deltas are sub-second.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@server.rtc_session(agent_name=config.AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    """Entrypoint triggered when LiveKit assigns an explicit dispatch to this worker."""
    logger.info("Job assigned: job_id=%s, room=%s", ctx.job.id, ctx.job.room.name)

    # 1. Parse Dispatch Metadata
    # WHY metadata: Serialized by FastAPI backend during call creation containing call_id and agent_id.
    # Allows the worker to resolve the exact agent configuration without extra external discovery RPCs.
    metadata_raw = ctx.job.metadata or "{}"
    try:
        metadata = json.loads(metadata_raw)
        call_id = metadata["call_id"]
        agent_id = metadata["agent_id"]
    except Exception as exc:
        logger.error("Failed to parse job metadata '%s': %s", metadata_raw, exc)
        return

    logger.info("Processing call_id=%s, agent_id=%s", call_id, agent_id)

    # 2. Fetch Agent Profile from Database via Backend API
    try:
        agent_profile = await api_client.get_agent(agent_id)
        logger.info(
            "Loaded agent profile: name='%s', voice='%s', model='%s'",
            agent_profile.get("name"),
            agent_profile.get("voice"),
            agent_profile.get("model"),
        )
    except APIClientError as exc:
        logger.error("Failed to fetch agent profile from API: %s", exc)
        return

    # 3. Connect Worker to LiveKit Room
    await ctx.connect()
    logger.info("Worker successfully joined room %s", ctx.room.name)

    # 4. Handle Race Condition: Customer might ALREADY be in the room
    # WHY: If customer connected slightly before or while the worker was spinning up,
    # waiting only on 'participant_connected' would deadlock because the event already fired.
    customer_participant: rtc.RemoteParticipant | None = None
    for p in ctx.room.remote_participants.values():
        if p.identity.startswith("customer-"):
            customer_participant = p
            logger.info("Customer participant ALREADY present in room: %s", p.identity)
            break

    # If customer is not yet present, register listener and await their arrival
    if customer_participant is None:
        logger.info("Customer not yet in room. Waiting for customer to connect...")
        customer_joined_fut: asyncio.Future[rtc.RemoteParticipant] = asyncio.get_running_loop().create_future()

        @ctx.room.on("participant_connected")
        def on_participant_connected(p: rtc.RemoteParticipant) -> None:
            if p.identity.startswith("customer-") and not customer_joined_fut.done():
                logger.info("Customer connected to room: %s", p.identity)
                customer_joined_fut.set_result(p)

        customer_participant = await customer_joined_fut

    worker_saw_at = datetime.now(timezone.utc)

    # 5. Determine Call Start Time (Never record later than customer's real join)
    # WHY joined_at: LiveKit server records the real millisecond timestamp when the customer
    # established their WebRTC session. Using participant.joined_at ensures metrics reflect true
    # customer wait time without adding worker initialization lag.
    customer_joined_at = customer_participant.joined_at
    if customer_joined_at is not None:
        call_started_iso = _format_iso(customer_joined_at)
        latency_diff = (worker_saw_at - customer_joined_at).total_seconds()
        logger.info(
            "Call start: customer server-side join=%s, worker saw customer=%s (worker detection latency: %.3fs)",
            call_started_iso,
            _format_iso(worker_saw_at),
            latency_diff,
        )
    else:
        call_started_iso = _format_iso(worker_saw_at)
        logger.info(
            "Call start: participant.joined_at unavailable; using worker saw customer time=%s",
            call_started_iso,
        )

    # Notify API backend that call is active
    try:
        await api_client.mark_call_started(call_id=call_id, started_at=call_started_iso)
    except APIClientError as exc:
        logger.warning("Could not mark call started in backend: %s", exc)

    # 6. Configure Voice AI Pipeline Components
    # STT: Deepgram nova-3 with endpointing
    stt_plugin = deepgram.STT(
        model="nova-3",
        language="en-US",
    )

    # TTS: Deepgram Aura voice specified in agent profile
    voice_name = agent_profile.get("voice") or "aura-2-andromeda-en"
    tts_plugin = deepgram.TTS(
        model=voice_name,
    )

    # LLM: Groq model wrapped in TrackingLLM to monitor full generation for barge-in auditing
    model_name = agent_profile.get("model") or "qwen/qwen3.8-27b"
    base_llm = groq.LLM(model=model_name)
    tracking_llm = TrackingLLM(base_llm)

    # VAD: Silero local neural voice activity detector
    vad_plugin = silero.VAD.load()

    # Create Voice Agent with instructions from database
    instructions = agent_profile.get("system_prompt", "You are a professional customer support agent.")
    agent = Agent(instructions=instructions)

    # Create AgentSession
    session = AgentSession(
        stt=stt_plugin,
        tts=tts_plugin,
        llm=tracking_llm,
        vad=vad_plugin,
    )

    # 7. Transcript and Interruption Tracking State
    transcript_seq = 0
    transcript_lock = asyncio.Lock()

    async def _record_transcript_line(speaker: str, text: str, started_at: str, ended_at: str) -> None:
        nonlocal transcript_seq
        if not text.strip():
            return
        async with transcript_lock:
            line_data = {
                "seq": transcript_seq,
                "speaker": speaker,
                "text": text.strip(),
                "started_at": started_at,
                "ended_at": ended_at,
            }
            transcript_seq += 1
            try:
                await api_client.upsert_transcript(call_id, [line_data])
            except Exception as exc:
                logger.error("Failed to stream transcript line: %s", exc)

    # 8. Register Conversation Item Hook for Barge-in and Transcripts
    @session.on("conversation_item_added")
    def on_conversation_item_added(ev: Any) -> None:
        item = ev.item
        now_iso = _format_iso(datetime.now(timezone.utc))

        if not isinstance(item, llm.ChatMessage):
            return

        text = item.text_content
        metrics = getattr(item, "metrics", None) or {}

        # Timing extraction:
        # For agent: metrics["started_speaking_at"] is recorded when the first audio frame begins playout
        # and metrics["stopped_speaking_at"] is recorded when playout completes or is interrupted.
        # For user: metrics["started_speaking_at"] is the start of the user speaking turn from VAD/STT.
        started_speaking_at = metrics.get("started_speaking_at")
        stopped_speaking_at = metrics.get("stopped_speaking_at")

        if started_speaking_at is not None:
            started_at_dt = datetime.fromtimestamp(started_speaking_at, tz=timezone.utc)
        elif getattr(item, "created_at", None) is not None:
            started_at_dt = datetime.fromtimestamp(item.created_at, tz=timezone.utc)
        else:
            started_at_dt = datetime.now(timezone.utc)

        if stopped_speaking_at is not None:
            ended_at_dt = datetime.fromtimestamp(stopped_speaking_at, tz=timezone.utc)
        else:
            ended_at_dt = datetime.now(timezone.utc)

        started_at_iso = _format_iso(started_at_dt)
        ended_at_iso = _format_iso(ended_at_dt)

        if item.role == "assistant":
            # Check for Interruption / Barge-in
            if getattr(item, "interrupted", False):
                # Correction 3: Log full generated LLM text vs kept text with interruption timestamp
                interruption_ts = _format_iso(datetime.now(timezone.utc))
                logger.info(
                    "[INTERRUPTION_DEBUG] Barge-in detected at %s! "
                    "FULL LLM generated: %r | KEPT in history/transcript: %r",
                    interruption_ts,
                    tracking_llm.latest_full_text,
                    text,
                )

            # Record assistant turn with exact playback timing
            asyncio.create_task(
                _record_transcript_line(
                    speaker="agent",
                    text=text,
                    started_at=started_at_iso,
                    ended_at=ended_at_iso,
                )
            )

        elif item.role == "user":
            # Record user turn with speech timing
            asyncio.create_task(
                _record_transcript_line(
                    speaker="user",
                    text=text,
                    started_at=started_at_iso,
                    ended_at=ended_at_iso,
                )
            )

    # 9. Link Session Exclusively to Customer Participant
    # WHY participant_identity: Ensures the agent's STT and VAD are bound strictly to customer audio,
    # ignoring any operator/supervisor who may join the room to listen or whisper.
    # WHY close_on_disconnect=True: If customer hangs up or disconnects, AgentSession terminates promptly.
    room_opts = room_io.RoomOptions(
        participant_identity=customer_participant.identity,
        close_on_disconnect=True,
    )

    # Start Session and await full RoomIO setup
    await session.start(agent, room=ctx.room, room_options=room_opts)

    # 10. Initial Agent Greeting with Recording Disclosure
    greeting = (
        "Hello! Thank you for calling customer support. "
        "Please note that this call is recorded for quality assurance. "
        "How can I help you today?"
    )
    # Deliver initial spoken greeting
    try:
        await session.say(greeting)
    except Exception as exc:
        logger.warning("Error saying initial greeting: %s", exc)

    # 11. Wait for Customer Disconnection / Call End
    disconnect_fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    @session.on("close")
    def on_session_close(ev: Any) -> None:
        logger.info("AgentSession close event received")
        if not disconnect_fut.done():
            disconnect_fut.set_result(None)

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(p: rtc.RemoteParticipant) -> None:
        if p.identity.startswith("customer-") and not disconnect_fut.done():
            logger.info("Customer participant disconnected: %s", p.identity)
            disconnect_fut.set_result(None)

    # Wait until customer disconnects or session closes
    await disconnect_fut

    ended_at_iso = _format_iso(datetime.now(timezone.utc))
    logger.info("Call session ended for call_id=%s at %s", call_id, ended_at_iso)

    # 12. Finalize Call in Backend
    try:
        await api_client.mark_call_ended(call_id=call_id, ended_at=ended_at_iso)
    except Exception as exc:
        logger.error("Error finalizing call ended state in API: %s", exc)


if __name__ == "__main__":
    # Support running via CLI: uv run python agent.py start / download-files
    cli.run_app(server)
