"""Script simulating a customer joining a LiveKit call room before the worker arrives.

Used to test and verify the race condition handling (Task Correction 4):
1. Connects to the room with the customer token.
2. Waits for worker participant to join.
3. Observes the worker's presence and track publications.
4. Cleanly disconnects after verification.
"""

import asyncio
import logging
import sys
from livekit import rtc

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [customer-sim] %(message)s")
logger = logging.getLogger("customer-sim")


async def simulate_customer(livekit_url: str, token: str, stay_seconds: float = 8.0) -> None:
    """Connect as customer, hold the room, and listen for agent worker arrival."""
    room = rtc.Room()

    agent_joined_event = asyncio.Event()

    @room.on("participant_connected")
    def on_participant_connected(p: rtc.RemoteParticipant) -> None:
        logger.info("Customer saw remote participant join: %s (%s)", p.identity, p.sid)
        if "agent" in p.identity.lower():
            agent_joined_event.set()

    @room.on("track_published")
    def on_track_published(pub: rtc.RemoteTrackPublication, p: rtc.RemoteParticipant) -> None:
        logger.info("Remote track published by %s: kind=%s, source=%s", p.identity, pub.kind, pub.source)

    logger.info("Customer connecting to %s...", livekit_url)
    await room.connect(livekit_url, token)
    logger.info("Customer connected! Local identity: %s, Room: %s", room.local_participant.identity, room.name)

    # Check if agent is already in the room
    for p in room.remote_participants.values():
        logger.info("Found existing participant: %s", p.identity)
        if "agent" in p.identity.lower():
            agent_joined_event.set()

    logger.info("Customer waiting in room (customer joined first)...")
    try:
        await asyncio.wait_for(agent_joined_event.wait(), timeout=stay_seconds)
        logger.info("SUCCESS: Customer observed worker join the room!")
        # Stay a bit longer to allow audio/greetings
        await asyncio.sleep(3.0)
    except asyncio.TimeoutError:
        logger.warning("Agent worker did not join within %s seconds", stay_seconds)
    finally:
        logger.info("Customer disconnecting...")
        await room.disconnect()
        logger.info("Customer disconnected.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python join_as_customer.py <livekit_url> <customer_token> [stay_seconds]")
        sys.exit(1)

    url = sys.argv[1]
    tok = sys.argv[2]
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    asyncio.run(simulate_customer(url, tok, dur))
