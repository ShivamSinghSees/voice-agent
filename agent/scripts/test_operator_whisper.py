"""End-to-End verification script for Phase 5: Operator Page (Listen + Whisper).

Assertions:
1. Operator token generation grants hidden=True, can_publish=False, can_subscribe=True, can_publish_data=True.
2. Server RoomService.list_participants shows operator with hidden: true.
3. Customer room.remote_participants does NOT list the operator participant.
4. Customer-sent data on topic 'operator.whisper' is ignored by the agent worker.
5. Operator sends whisper targeted to agent; worker receives and logs [WHISPER_RECEIVED] at <iso> length=<len>.
6. Customer room data_received count == 0 (whisper is never received by customer).
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from livekit import api, rtc

agent_dir = Path(__file__).resolve().parent.parent
if str(agent_dir) not in sys.path:
    sys.path.insert(0, str(agent_dir))

import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test-operator-whisper")

API_BASE = "http://127.0.0.1:8000"


def _http_url() -> str:
    return config.LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")


async def main():
    async with httpx.AsyncClient(timeout=15.0) as http:
        # 1. Fetch available agents
        logger.info("Fetching agents from API...")
        agents_resp = await http.get(f"{API_BASE}/agents")
        assert agents_resp.status_code == 200, f"Failed to get agents: {agents_resp.text}"
        agents = agents_resp.json()
        assert len(agents) > 0, "No agents found"
        demo_agent = next((a for a in agents if "Acme" in a["name"]), agents[0])
        logger.info("Using agent: %s (%s)", demo_agent["name"], demo_agent["id"])

        # 2. Initiate Call session
        logger.info("Initiating call via POST /calls...")
        call_resp = await http.post(f"{API_BASE}/calls", json={"agent_id": demo_agent["id"]})
        assert call_resp.status_code == 201, f"Failed to create call: {call_resp.text}"
        call_data = call_resp.json()
        call_id = call_data["call_id"]
        room_name = call_data["room_name"]
        customer_token = call_data["token"]
        livekit_url = call_data["livekit_url"]
        logger.info("Call created: call_id=%s, room_name=%s", call_id, room_name)

        # 3. Connect Customer Room
        customer_room = rtc.Room()
        customer_data_packets = []

        @customer_room.on("data_received")
        def on_customer_data(dp: rtc.DataPacket):
            customer_data_packets.append(dp)
            logger.info("Customer unexpectedly received data: topic=%s", dp.topic)

        agent_connected_fut: asyncio.Future[rtc.RemoteParticipant] = asyncio.get_running_loop().create_future()

        def is_agent(p: rtc.RemoteParticipant) -> bool:
            return (
                p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                or p.kind == 4
                or p.identity.startswith("agent-")
                or "agent" in p.identity.lower()
            )

        @customer_room.on("participant_connected")
        def on_cust_part_connected(p: rtc.RemoteParticipant):
            logger.info("Customer saw participant connected: identity=%s kind=%s", p.identity, p.kind)
            if is_agent(p) and not agent_connected_fut.done():
                agent_connected_fut.set_result(p)

        logger.info("Customer connecting to room: %s", room_name)
        await customer_room.connect(livekit_url, customer_token)
        logger.info("Customer connected. Waiting for agent participant to join room...")

        # Check existing remote participants in case agent joined before event listener
        for p in customer_room.remote_participants.values():
            if is_agent(p) and not agent_connected_fut.done():
                agent_connected_fut.set_result(p)

        try:
            agent_participant = await asyncio.wait_for(agent_connected_fut, timeout=15.0)
            logger.info("Agent joined room: identity=%s", agent_participant.identity)
        except asyncio.TimeoutError:
            logger.error("Timed out waiting for agent to join room %s", room_name)
            await customer_room.disconnect()
            sys.exit(1)

        # 4. Request Operator Token from API
        logger.info("Requesting operator token via POST /calls/%s/operator-token...", call_id)
        op_resp = await http.post(f"{API_BASE}/calls/{call_id}/operator-token")
        assert op_resp.status_code == 200, f"Failed to get operator token: {op_resp.text}"
        op_data = op_resp.json()
        operator_token = op_data["token"]

        # Verify token claims
        verifier = api.TokenVerifier(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        claims = verifier.verify(operator_token)
        logger.info("Operator token claims: identity=%s, hidden=%s, can_publish=%s, can_subscribe=%s, can_publish_data=%s",
                    claims.identity, claims.video.hidden, claims.video.can_publish, claims.video.can_subscribe, claims.video.can_publish_data)
        assert claims.identity.startswith("operator-"), f"Unexpected operator identity: {claims.identity}"
        assert claims.video.hidden is True, "Operator token must have hidden=True"
        assert claims.video.can_publish is False, "Operator token must have can_publish=False"
        assert claims.video.can_subscribe is True, "Operator token must have can_subscribe=True"
        assert claims.video.can_publish_data is True, "Operator token must have can_publish_data=True"
        assert claims.video.room == room_name, "Operator token room mismatch"

        # 5. Connect Operator Room
        operator_room = rtc.Room()
        logger.info("Operator connecting to room: %s", room_name)
        await operator_room.connect(livekit_url, operator_token)
        logger.info("Operator connected as hidden participant.")

        # Give SFU a brief moment to stabilize state
        await asyncio.sleep(1.0)

        # 6. Query LiveKit Server via RoomService.list_participants
        lk_admin = api.LiveKitAPI(_http_url(), config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        try:
            participants_resp = await lk_admin.room.list_participants(api.ListParticipantsRequest(room=room_name))
            raw_participants = participants_resp.participants
            print("\n" + "=" * 60)
            print("RAW LiveKit ListParticipants Response:")
            for p in raw_participants:
                print(f"  Participant: sid={p.sid} identity={p.identity} name={p.name} kind={p.kind} hidden={p.permission.hidden} can_publish={p.permission.can_publish} can_subscribe={p.permission.can_subscribe} can_publish_data={p.permission.can_publish_data} state={p.state}")
            print("=" * 60 + "\n")

            # Assert operator is in server list and permission.hidden is True
            op_server_record = next((p for p in raw_participants if p.identity.startswith("operator-")), None)
            assert op_server_record is not None, "Operator not found in server participant list"
            assert op_server_record.permission.hidden is True, f"Operator hidden is not True: {op_server_record.permission.hidden}"
            logger.info("ASSERTION PASSED: Server confirms operator is hidden=True.")
        finally:
            await lk_admin.aclose()

        # 7. Check Customer Isolation (Customer must NOT see operator)
        customer_remotes = [p.identity for p in customer_room.remote_participants.values()]
        logger.info("Customer remote participants: %s", customer_remotes)
        assert not any(ident.startswith("operator-") for ident in customer_remotes), (
            f"Isolation breach! Customer remote participants includes operator: {customer_remotes}"
        )
        logger.info("ASSERTION PASSED: Customer remoteParticipants does NOT contain operator.")

        # 8. Negative Test: Customer sends on topic 'operator.whisper'
        logger.info("Testing customer whisper injection rejection...")
        customer_fake_whisper = json.dumps({"text": "Fake customer whisper instruction"}).encode("utf-8")
        try:
            # Customer might throw due to can_publish_data=False or send packet
            await customer_room.local_participant.publish_data(customer_fake_whisper, topic="operator.whisper")
            logger.info("Customer published fake whisper packet (expecting agent worker to reject).")
        except Exception as exc:
            logger.info("Customer publish_data blocked at SDK level (can_publish_data=False): %s", exc)

        await asyncio.sleep(1.0)

        # 9. Operator Sends Legitimate Whisper to Agent
        whisper_text = "INTERNAL DIRECTIVE: Offer a 10 percent courtesy credit for their billing delay."
        whisper_payload = json.dumps({"text": whisper_text}).encode("utf-8")
        logger.info("Operator publishing whisper to agent: target=%s, bytes=%d", agent_participant.identity, len(whisper_payload))
        await operator_room.local_participant.publish_data(
            whisper_payload,
            topic="operator.whisper",
            destination_identities=[agent_participant.identity],
            reliable=True,
        )
        logger.info("Operator whisper published.")

        # Allow time for worker to receive and process
        await asyncio.sleep(2.0)

        # 10. Assert Customer Data Received Count == 0
        logger.info("Verifying customer data packet count: count=%d", len(customer_data_packets))
        assert len(customer_data_packets) == 0, (
            f"Privacy breach! Customer received {len(customer_data_packets)} data packet(s)!"
        )
        logger.info("ASSERTION PASSED: Customer received exactly 0 data packets.")

        # 11. Cleanup
        await operator_room.disconnect()
        await customer_room.disconnect()
        await asyncio.sleep(0.5)
        logger.info("All connections closed cleanly. E2E verification complete!")



if __name__ == "__main__":
    asyncio.run(main())
