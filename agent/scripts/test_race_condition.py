"""End-to-end live test for the LiveKit voice agent (self-hosted server, real providers).

Flow:
1. POST /calls for the "Acme Telecom Support" agent (falls back to the first agent).
2. Customer joins the room FIRST (race-condition check).
3. Worker subprocess is started AFTER the customer is present.
4. Server-side evidence: RoomService.list_participants shows the agent identity with a published audio track.
5. Customer speaks a real utterance (Deepgram TTS audio published as the customer's microphone track),
   so the transcript contains a USER line produced by the real STT path.
6. Customer disconnects; GET /calls/{id} is printed in full plus transcript offsets vs call.started_at.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import httpx
from livekit import api, rtc

agent_dir = Path(__file__).resolve().parent.parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test-race")

CUSTOMER_UTTERANCE = "Hi, I was overcharged last month. Can I get a refund on my last bill?"
SAMPLE_RATE = 24000


def _http_url() -> str:
    return config.LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _list_participants(room_name: str) -> list:
    lk = api.LiveKitAPI(_http_url(), config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
    try:
        resp = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return list(resp.participants)
    finally:
        await lk.aclose()


async def _print_published_tracks(room_name: str) -> bool:
    """Print every participant + published tracks as seen by the SERVER. Returns True if agent has audio."""
    participants = await _list_participants(room_name)
    agent_has_audio = False
    print(f"[ROOMSERVICE] list_participants(room={room_name}) -> {len(participants)} participants")
    for p in participants:
        print(f"[ROOMSERVICE]   identity={p.identity} kind={p.kind} joined_at={p.joined_at} joined_at_ms={p.joined_at_ms}")
        for t in p.tracks:
            print(
                f"[ROOMSERVICE]     track sid={t.sid} type={api.TrackType.Name(t.type)} "
                f"source={api.TrackSource.Name(t.source)} muted={t.muted} mime={t.mime_type}"
            )
            if not p.identity.startswith("customer-") and t.type == api.TrackType.AUDIO:
                agent_has_audio = True
    return agent_has_audio


async def _speak_as_customer(room: rtc.Room) -> None:
    """Synthesize the customer utterance with Deepgram TTS and publish it as the customer's mic track."""
    from livekit.plugins import deepgram

    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("customer-mic", source)
    opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(track, opts)
    logger.info("Customer published microphone track; speaking: %r", CUSTOMER_UTTERANCE)

    async with aiohttp.ClientSession() as session:
        tts = deepgram.TTS(model="aura-asteria-en", sample_rate=SAMPLE_RATE, http_session=session)
        async for ev in tts.synthesize(CUSTOMER_UTTERANCE):
            await source.capture_frame(ev.frame)
        await tts.aclose()

    # 1.5 s of silence so VAD / turn detection sees the end of the user turn
    silence = rtc.AudioFrame.create(SAMPLE_RATE, 1, SAMPLE_RATE // 100)
    for _ in range(150):
        await source.capture_frame(silence)
    logger.info("Customer finished speaking")


async def run_race_test() -> None:
    api_url = config.API_BASE_URL

    async with httpx.AsyncClient(timeout=10.0) as client:
        agents = (await client.get(f"{api_url}/agents")).json()
        agent = next((a for a in agents if a["name"] == "Acme Telecom Support"), agents[0])
        agent_id = agent["id"]
        logger.info("Using agent: %s (%s) model=%s", agent["name"], agent_id, agent["model"])

        call_resp = await client.post(f"{api_url}/calls", json={"agent_id": agent_id})
        assert call_resp.status_code == 201, call_resp.text
        call_data = call_resp.json()
        call_id, room_name, customer_token = call_data["call_id"], call_data["room_name"], call_data["token"]
        logger.info("Call created: call_id=%s, room=%s", call_id, room_name)

    # Customer joins FIRST
    customer_room = rtc.Room()
    agent_seen: asyncio.Future = asyncio.get_running_loop().create_future()
    agent_audio_subscribed = asyncio.Event()

    @customer_room.on("participant_connected")
    def _on_pc(p: rtc.RemoteParticipant) -> None:
        logger.info("Customer saw participant_connected: %s", p.identity)
        if not p.identity.startswith("customer-") and not agent_seen.done():
            agent_seen.set_result(p)

    @customer_room.on("track_subscribed")
    def _on_ts(track: rtc.Track, pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        logger.info("Customer subscribed to %s track from %s (sid=%s)", track.kind, p.identity, pub.sid)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            agent_audio_subscribed.set()

    await customer_room.connect(config.LIVEKIT_URL, customer_token)
    assert customer_room.local_participant.identity == f"customer-{call_id}"
    assert not customer_room.remote_participants, "Expected NO agent in room yet"
    logger.info("Customer connected FIRST as %s", customer_room.local_participant.identity)

    # Start worker AFTER customer is present
    proc = await asyncio.create_subprocess_exec(
        "uv", "--directory", str(agent_dir), "run", "python", "agent.py", "start",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    flags = {"already_present": False, "join_logged": False}
    registered: asyncio.Future = asyncio.get_running_loop().create_future()

    async def read_worker_logs() -> None:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip()
            # Only echo the human-readable lines (the worker also emits a JSON copy of each line)
            if text and not text.startswith("{"):
                print(f"[WORKER-LOG] {text}")
            if "registered worker" in text and not registered.done():
                registered.set_result(True)
            if "Customer participant ALREADY present in room" in text:
                flags["already_present"] = True
            if "customer server-side join" in text:
                flags["join_logged"] = True

    log_task = asyncio.create_task(read_worker_logs())
    try:
        await asyncio.wait_for(registered, timeout=20.0)

        # The room's dispatch was created atomically with the room while NO worker was registered,
        # and the server does not re-offer it when a worker registers later. Re-dispatch explicitly.
        lk = api.LiveKitAPI(_http_url(), config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        try:
            await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                agent_name=config.AGENT_NAME, room=room_name,
                metadata=json.dumps({"call_id": call_id, "agent_id": agent_id}),
            ))
            dispatches = await lk.agent_dispatch.list_dispatch(room_name)
            print(f"[DISPATCH] list_dispatch({room_name}) -> {len(dispatches)}")
            for d in dispatches:
                print(f"[DISPATCH]   id={d.id} agent_name={d.agent_name!r} metadata={d.metadata!r}")
        finally:
            await lk.aclose()

        agent_p = await asyncio.wait_for(agent_seen, timeout=20.0)
        logger.info("Agent participant joined: %s", agent_p.identity)
        await asyncio.wait_for(agent_audio_subscribed.wait(), timeout=20.0)

        # Server-side proof of the agent's published audio track
        await asyncio.sleep(1.0)
        agent_has_audio = await _print_published_tracks(room_name)

        # Let the greeting finish, then speak as the customer and wait for the agent's reply
        await asyncio.sleep(9.0)
        await _speak_as_customer(customer_room)
        await asyncio.sleep(14.0)
    finally:
        await customer_room.disconnect()
        logger.info("Customer disconnected")
        await asyncio.sleep(5.0)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
        await log_task

    async with httpx.AsyncClient(timeout=10.0) as client:
        final_call = (await client.get(f"{api_url}/calls/{call_id}")).json()
    print("[CALL-JSON] " + json.dumps(final_call, indent=2))

    started = _parse_iso(final_call["started_at"])
    for row in final_call["transcript"] or []:
        s, e = _parse_iso(row["started_at"]), _parse_iso(row["ended_at"])
        print(
            f"[OFFSET] seq={row['seq']} {row['speaker']:<5} start=+{(s - started).total_seconds():.3f}s "
            f"end=+{(e - started).total_seconds():.3f}s dur={(e - s).total_seconds():.3f}s"
        )

    assert final_call["status"] == "ended"
    assert final_call["transcript"], "transcript must be present after end"
    assert agent_has_audio, "agent participant had no published AUDIO track according to RoomService"
    assert flags["already_present"], "worker did not detect pre-joined customer"
    assert flags["join_logged"], "worker did not log customer server-side join"
    first_agent = next(r for r in final_call["transcript"] if r["speaker"] == "agent")
    offset = (_parse_iso(first_agent["started_at"]) - started).total_seconds()
    assert 0 <= offset < 30, f"first agent line offset {offset}s is not sensible"
    logger.info("ALL ASSERTIONS PASSED (first agent line offset = %.3fs)", offset)


if __name__ == "__main__":
    asyncio.run(run_race_test())
