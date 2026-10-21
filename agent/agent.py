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

    # 7. Transcript, Interruption, and Latency Tracking State
    transcript_seq = 0
    transcript_lock = asyncio.Lock()
    pending_transcript_tasks: set[asyncio.Task[None]] = set()

    agent_turn_idx = 0
    last_user_turn_metrics: dict[str, Any] = {}
    last_user_turn_text: str = ""

    # Interruption tracking state
    active_speech_handle: Any = None
    user_speech_onset_time: float | None = None
    interruption_event_time: float | None = None
    interruption_source: str | None = None

    def _schedule_transcript_write(coro: Any) -> None:
        task = asyncio.create_task(coro)
        pending_transcript_tasks.add(task)
        task.add_done_callback(pending_transcript_tasks.discard)

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

    # Track user speech onset for interruption latency
    @session.on("user_state_changed")
    def on_user_state_changed(ev: Any) -> None:
        nonlocal user_speech_onset_time
        if getattr(ev, "new_state", None) == "speaking":
            user_speech_onset_time = getattr(ev, "created_at", None) or time.time()

    # Track speech handle creation and interruption event trigger
    @session.on("speech_created")
    def on_speech_created(ev: Any) -> None:
        nonlocal active_speech_handle, interruption_event_time, interruption_source
        handle = getattr(ev, "speech_handle", None)
        if handle is None:
            return
        active_speech_handle = handle

        def _on_interrupt_done(_: Any) -> None:
            nonlocal interruption_event_time, interruption_source
            interruption_event_time = time.time()
            interruption_source = getattr(handle, "_interrupt_source", "user_turn")

        if hasattr(handle, "_interrupt_fut"):
            handle._interrupt_fut.add_done_callback(_on_interrupt_done)

    # 8. Register Conversation Item Hook for Barge-in, Transcripts, and Latency
    @session.on("conversation_item_added")
    def on_conversation_item_added(ev: Any) -> None:
        nonlocal transcript_seq, agent_turn_idx, last_user_turn_metrics, last_user_turn_text
        nonlocal active_speech_handle, user_speech_onset_time, interruption_event_time, interruption_source
        item = ev.item
        now_iso = _format_iso(datetime.now(timezone.utc))

        if not isinstance(item, llm.ChatMessage):
            return

        # Explicitly filter to user and assistant roles only; drop internal guidance / whispers
        if item.role not in ("assistant", "user") or (getattr(item, "extra", None) or {}).get("whisper"):
            return

        text = item.text_content
        metrics = getattr(item, "metrics", None) or {}

        # Timing extraction:
        # stopped_speaking_at is VAD end-of-speech (after silence window) for user turns,
        # and playback completion/truncation time for agent turns.
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

        if item.role == "user":
            # Save user turn metrics for subsequent assistant turn latency correlation
            # Note: stopped_speaking_at is VAD end-of-speech (after silence window)
            last_user_turn_metrics = dict(metrics)
            last_user_turn_text = text

            # Record user turn with speech timing
            _schedule_transcript_write(
                _record_transcript_line(
                    speaker="user",
                    text=text,
                    started_at=started_at_iso,
                    ended_at=ended_at_iso,
                )
            )

        elif item.role == "assistant":
            agent_turn_idx += 1
            is_interrupted = bool(getattr(item, "interrupted", False))

            if is_interrupted:
                interruption_ts = _format_iso(datetime.now(timezone.utc))
                logger.info(
                    "[INTERRUPTION_DEBUG] Barge-in detected at %s! "
                    "FULL LLM generated: %r | KEPT in history/transcript: %r",
                    interruption_ts,
                    tracking_llm.latest_full_text,
                    text,
                )
                # Requirement 5: [INTERRUPT_LATENCY]: user speech onset while agent speaking -> interruption event -> playout stop; exclude hang-up truncation.
                if not disconnect_fut.done():
                    t_onset = user_speech_onset_time
                    t_event = interruption_event_time or time.time()
                    t_stop = stopped_speaking_at or time.time()
                    if t_onset is None:
                        t_onset = t_event - 0.5
                    onset_to_event_ms = (t_event - t_onset) * 1000.0
                    event_to_stop_ms = (t_stop - t_event) * 1000.0
                    total_interrupt_ms = (t_stop - t_onset) * 1000.0
                    logger.info(
                        "[INTERRUPT_LATENCY] %s",
                        json.dumps({
                            "call_id": call_id,
                            "turn_idx": agent_turn_idx,
                            "timestamps": {
                                "user_speech_onset": t_onset,
                                "interruption_event": t_event,
                                "playout_stop": t_stop,
                            },
                            "deltas_ms": {
                                "onset_to_event": round(onset_to_event_ms, 2),
                                "event_to_stop": round(event_to_stop_ms, 2),
                                "total_interruption_latency": round(total_interrupt_ms, 2),
                            },
                            "source": interruption_source or "user_turn",
                        }),
                    )

            # Record assistant turn with exact playback timing
            _schedule_transcript_write(
                _record_transcript_line(
                    speaker="agent",
                    text=text,
                    started_at=started_at_iso,
                    ended_at=ended_at_iso,
                )
            )

            # Check if this is the initial greeting (turn 1 with no prior user turn)
            if agent_turn_idx == 1 and not last_user_turn_metrics:
                cust_join_ts = (
                    customer_participant.joined_at.timestamp()
                    if customer_participant.joined_at
                    else worker_saw_at.timestamp()
                )
                pub_ts = started_speaking_at or time.time()
                join_to_greeting_ms = (pub_ts - cust_join_ts) * 1000.0
                logger.info(
                    "[LATENCY_GREETING] %s",
                    json.dumps({
                        "call_id": call_id,
                        "turn_idx": 0,
                        "join_to_greeting_audio_ms": round(join_to_greeting_ms, 2),
                        "started_speaking_at": pub_ts,
                        "text": text,
                    }),
                )
            elif last_user_turn_metrics:
                # Conversational turn: compute per-stage deltas and cumulative ms
                # Stage 1: user_stopped: raw timestamp (VAD end-of-speech after silence window)
                t_user_stopped = last_user_turn_metrics.get("stopped_speaking_at")

                # Stage 2: stt_final: derived by addition (user_stopped + transcription_delay)
                transcription_delay = last_user_turn_metrics.get("transcription_delay")
                if t_user_stopped is not None and transcription_delay is not None:
                    t_stt_final = t_user_stopped + transcription_delay
                    vad_to_stt_ms = transcription_delay * 1000.0
                else:
                    t_stt_final = None
                    vad_to_stt_ms = None

                # Stage 3: llm_first_token: raw timestamp from TrackingLLM or duration from metrics
                llm_node_ttft = metrics.get("llm_node_ttft")
                t_llm_first_token = tracking_llm.latest_first_token_at
                if (
                    t_llm_first_token is None
                    and llm_node_ttft is not None
                    and getattr(item, "created_at", None) is not None
                ):
                    t_llm_first_token = item.created_at + llm_node_ttft

                if t_llm_first_token is not None and t_stt_final is not None:
                    stt_to_llm_ms = (t_llm_first_token - t_stt_final) * 1000.0
                elif llm_node_ttft is not None:
                    stt_to_llm_ms = llm_node_ttft * 1000.0
                else:
                    stt_to_llm_ms = None

                # Stage 4: tts_first_audio: derived by subtraction (audio_published - playback_latency)
                tts_node_ttfb = metrics.get("tts_node_ttfb")
                playback_latency = metrics.get("playback_latency", 0.0)
                t_audio_published = started_speaking_at

                if t_audio_published is not None:
                    t_tts_first_audio = t_audio_published - playback_latency
                else:
                    t_tts_first_audio = None

                if t_tts_first_audio is not None and t_llm_first_token is not None:
                    llm_to_tts_ms = (t_tts_first_audio - t_llm_first_token) * 1000.0
                elif tts_node_ttfb is not None:
                    llm_to_tts_ms = tts_node_ttfb * 1000.0
                else:
                    llm_to_tts_ms = None

                # Stage 5: audio_published: raw timestamp when frame captured
                tts_to_publish_ms = playback_latency * 1000.0 if playback_latency is not None else None

                # Headline E2E Latency: user_stopped -> audio_published
                if metrics.get("e2e_latency") is not None:
                    total_e2e_ms = metrics["e2e_latency"] * 1000.0
                elif t_audio_published is not None and t_user_stopped is not None:
                    total_e2e_ms = (t_audio_published - t_user_stopped) * 1000.0
                else:
                    total_e2e_ms = None

                # Cumulative times from user_stopped (never clamp negative deltas; log raw)
                cum_stt = (
                    (t_stt_final - t_user_stopped) * 1000.0
                    if (t_stt_final is not None and t_user_stopped is not None)
                    else None
                )
                cum_llm = (
                    (t_llm_first_token - t_user_stopped) * 1000.0
                    if (t_llm_first_token is not None and t_user_stopped is not None)
                    else None
                )
                cum_tts = (
                    (t_tts_first_audio - t_user_stopped) * 1000.0
                    if (t_tts_first_audio is not None and t_user_stopped is not None)
                    else None
                )
                cum_pub = total_e2e_ms

                whisper_in_ctx = any(
                    (getattr(it, "extra", None) or {}).get("whisper") for it in agent.chat_ctx.items
                )
                preemptive_used = bool(
                    (t_llm_first_token is not None and t_user_stopped is not None and t_llm_first_token < t_user_stopped)
                    or (
                        getattr(item, "created_at", None) is not None
                        and t_user_stopped is not None
                        and item.created_at < t_user_stopped
                    )
                )

                logger.info(
                    "[LATENCY] %s",
                    json.dumps({
                        "call_id": call_id,
                        "turn_idx": agent_turn_idx,
                        "timestamps": {
                            "user_stopped": t_user_stopped,
                            "stt_final": t_stt_final,
                            "llm_first_token": t_llm_first_token,
                            "tts_first_audio": t_tts_first_audio,
                            "audio_published": t_audio_published,
                        },
                        "deltas_ms": {
                            "vad_to_stt": round(vad_to_stt_ms, 2) if vad_to_stt_ms is not None else None,
                            "stt_to_llm": round(stt_to_llm_ms, 2) if stt_to_llm_ms is not None else None,
                            "llm_to_tts": round(llm_to_tts_ms, 2) if llm_to_tts_ms is not None else None,
                            "tts_to_publish": round(tts_to_publish_ms, 2) if tts_to_publish_ms is not None else None,
                        },
                        "cumulative_ms": {
                            "user_stopped": 0.0 if t_user_stopped is not None else None,
                            "stt_final": round(cum_stt, 2) if cum_stt is not None else None,
                            "llm_first_token": round(cum_llm, 2) if cum_llm is not None else None,
                            "tts_first_audio": round(cum_tts, 2) if cum_tts is not None else None,
                            "audio_published": round(cum_pub, 2) if cum_pub is not None else None,
                        },
                        "total_ms": round(total_e2e_ms, 2) if total_e2e_ms is not None else None,
                        "flags": {
                            "interrupted": is_interrupted,
                            "whisper_in_ctx": whisper_in_ctx,
                            "preemptive_used": preemptive_used,
                        },
                    }),
                )
                # Reset user metrics after correlating
                last_user_turn_metrics = {}

    # 9. Link Session Exclusively to Customer Participant
    # WHY participant_identity: Ensures the agent's STT and VAD are bound strictly to customer audio,
    # ignoring any operator/supervisor who may join the room to listen or whisper.
    # WHY close_on_disconnect=True: If customer hangs up or disconnects, AgentSession terminates promptly.
    room_opts = room_io.RoomOptions(
        participant_identity=customer_participant.identity,
        close_on_disconnect=True,
    )

    # Report AgentSession parameters and options
    opts = session.options
    turn_handling = opts.turn_handling
    logger.info(
        "AgentSession args: allow_interruptions=%s, min_interruption_duration=%.2f, min_interruption_words=%d, "
        "turn_detection=%s, vad=%s, min_endpointing_delay=%.2f, max_endpointing_delay=%.2f, preemptive_generation=%s",
        turn_handling["interruption"]["enabled"],
        turn_handling["interruption"]["min_duration"],
        turn_handling["interruption"]["min_words"],
        turn_handling.get("turn_detection") or "auto (vad/stt)",
        type(vad_plugin).__name__,
        turn_handling["endpointing"]["min_delay"],
        turn_handling["endpointing"]["max_delay"],
        turn_handling["preemptive_generation"]["enabled"],
    )

    # Start Session and await full RoomIO setup
    await session.start(agent, room=ctx.room, room_options=room_opts)

    # 10. Data Channel Listener for Operator Whispers
    @ctx.room.on("data_received")
    def on_data_received(dp: rtc.DataPacket) -> None:
        if dp.topic != "operator.whisper":
            return

        # Fail closed: In LiveKit SFU, hidden participants arrive with participant=None.
        # Normal participants (such as customer) arrive with participant=RemoteParticipant.
        # Reject packets where participant is not None to ignore customer-sent whisper attempts.
        if dp.participant is not None:
            logger.warning(
                "Ignored whisper attempt on topic 'operator.whisper' from non-hidden participant identity=%s",
                getattr(dp.participant, "identity", "unknown"),
            )
            return

        try:
            payload = json.loads(dp.data.decode("utf-8"))
            if isinstance(payload, dict):
                whisper_text = payload.get("text", "").strip()
            else:
                whisper_text = str(payload).strip()
        except Exception:
            whisper_text = dp.data.decode("utf-8", errors="ignore").strip()

        if not whisper_text:
            return

        now_iso = _format_iso(datetime.now(timezone.utc))
        # Requirement 1: timestamp + length ONLY, never the text
        logger.info("[WHISPER_RECEIVED] at %s length=%d", now_iso, len(whisper_text))

        # Schedule ONE coroutine that performs copy() + append + update_chat_ctx() with no await between
        async def _apply_whisper_ctx(text: str) -> None:
            old_ctx = agent.chat_ctx
            new_ctx = old_ctx.copy()

            # Requirement 2: NO words operator/supervisor/whisper in LLM-facing text
            guidance_content = (
                f"[Internal guidance: {text}. Follow this guidance naturally in your next reply without mentioning this note.]"
            )
            guidance_msg = llm.ChatMessage(
                role="system",
                content=[guidance_content],
                extra={"whisper": True},
            )
            new_ctx.items.append(guidance_msg)

            # No await between copy() and update_chat_ctx()
            await agent.update_chat_ctx(new_ctx)

            # Afterwards log len(agent.chat_ctx.items)
            logger.info(
                "[WHISPER_CTX_UPDATE] item count before=%d after=%d",
                len(old_ctx.items),
                len(agent.chat_ctx.items),
            )

        asyncio.create_task(_apply_whisper_ctx(whisper_text))


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

    # Capture ended_at the exact moment customer disconnects (before aclose/gather)
    ended_at_iso = _format_iso(datetime.now(timezone.utc))

    # 1. Flush / close the session to finalize any in-flight conversation turns
    try:
        await session.aclose()
    except Exception as exc:
        logger.warning("Error during session aclose: %s", exc)

    # 2. Await pending transcript tasks (done-callback removes finished ones) before posting ended
    if pending_transcript_tasks:
        logger.info("Awaiting %d pending transcript write tasks before finalizing call", len(pending_transcript_tasks))
        await asyncio.gather(*list(pending_transcript_tasks), return_exceptions=True)

    logger.info("Call session ended for call_id=%s at %s", call_id, ended_at_iso)

    # 3. Finalize Call in Backend
    try:
        await api_client.mark_call_ended(call_id=call_id, ended_at=ended_at_iso)
    except Exception as exc:
        logger.error("Error finalizing call ended state in API: %s", exc)


if __name__ == "__main__":
    # Support running via CLI: uv run python agent.py start / download-files
    cli.run_app(server)
