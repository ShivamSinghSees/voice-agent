"""Service wrapper for interacting with the self-hosted LiveKit server.

Handles RoomService room creation, explicit agent dispatch, participant token generation,
and dispatch inspection with retries, timeouts, and comprehensive commentary explaining WHY.
"""

import asyncio
import json
import logging
from typing import Any
from aiohttp import ClientError
from livekit import api
from app import config

logger = logging.getLogger(__name__)


class LiveKitServiceError(Exception):
    """Raised when communication with LiveKit server fails after retries."""
    pass


def _get_http_url() -> str:
    """LiveKit SDK requires an HTTP/HTTPS scheme for REST/RPC endpoints,

    whereas client browsers and agents connect via ws:// or wss://.
    """
    url = config.LIVEKIT_URL
    if url.startswith("ws://"):
        return url.replace("ws://", "http://", 1)
    if url.startswith("wss://"):
        return url.replace("wss://", "https://", 1)
    return url


def get_livekit_api() -> api.LiveKitAPI:
    """Instantiate a LiveKitAPI client using the HTTP REST endpoint and server credentials."""
    return api.LiveKitAPI(
        url=_get_http_url(),
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )


async def create_room_and_dispatch(room_name: str, call_id: str, agent_id: str) -> str:
    """Create a LiveKit room, create an explicit agent dispatch, and issue a customer token.

    Retries once on transient network/timeout failure.
    Raises LiveKitServiceError if unreachable or rejected.
    """
    attempt = 0
    max_attempts = 2

    while attempt < max_attempts:
        attempt += 1
        lkapi: api.LiveKitAPI | None = None
        try:
            # Initialize LiveKit REST API client with configured server credentials
            lkapi = get_livekit_api()

            dispatch_metadata = json.dumps({"call_id": call_id, "agent_id": agent_id})

            # 1. Create Room with empty_timeout=60 and explicit agent dispatch
            # WHY empty_timeout=60: If a customer triggers call creation but closes the tab without joining,
            # or leaves prematurely, LiveKit automatically cleans up and terminates the room after 60 seconds
            # rather than leaving orphaned rooms and allocated media ports lingering on the SFU.
            # WHY agents in CreateRoomRequest: Passing RoomAgentDispatch in CreateRoomRequest creates the
            # explicit agent dispatch atomically at room creation. If CreateRoomRequest is called without
            # agents, LiveKit server automatically registers an unassigned empty dispatch (agent_name=""),
            # causing duplicate dispatches when create_dispatch is subsequently called.
            room_req = api.CreateRoomRequest(
                name=room_name,
                empty_timeout=60,
                max_participants=5,
                agents=[
                    api.RoomAgentDispatch(
                        agent_name=config.AGENT_NAME,
                        metadata=dispatch_metadata,
                    )
                ],
            )
            # Timeout guard of 5.0 seconds prevents blocking backend event loop if LiveKit is down
            await asyncio.wait_for(lkapi.room.create_room(room_req), timeout=5.0)

            # 2. Mint Customer AccessToken
            # WHY identity="customer-<call_id>": Uniquely attributes participant identity in server logs and WebRTC SDP.
            # WHY room=room_name: Scopes permissions strictly to this specific call room; cannot join any other room.
            # WHY can_publish=True & can_publish_sources=["microphone"]: Restricts media publish rights strictly to mic audio.
            # WHY can_publish_data=False: Disables data channel publication so customers cannot inject spoofed whisper messages.
            # WHY can_subscribe=True: Allows customer to receive the agent's published audio track.
            customer_token = (
                api.AccessToken(
                    api_key=config.LIVEKIT_API_KEY,
                    api_secret=config.LIVEKIT_API_SECRET,
                )
                .with_identity(f"customer-{call_id}")
                .with_name("Customer")
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=room_name,
                        can_publish=True,
                        can_subscribe=True,
                        can_publish_data=False,
                        can_publish_sources=["microphone"],
                    )
                )
                .to_jwt()
            )

            return customer_token

        except (ClientError, asyncio.TimeoutError, ConnectionRefusedError) as exc:
            logger.warning(
                "Transient error connecting to LiveKit on attempt %d/%d: %s",
                attempt,
                max_attempts,
                exc,
            )
            if attempt < max_attempts:
                await asyncio.sleep(0.5)
            else:
                raise LiveKitServiceError(
                    f"LiveKit server unreachable after {max_attempts} attempts: {exc}"
                ) from exc
        except Exception as exc:
            logger.error("Non-retriable error communicating with LiveKit: %s", exc)
            raise LiveKitServiceError(f"Failed to setup LiveKit call session: {exc}") from exc
        finally:
            if lkapi is not None:
                await lkapi.aclose()

    raise LiveKitServiceError("Unexpected loop exit in create_room_and_dispatch")


async def list_agent_dispatches(room_name: str) -> list[dict[str, Any]]:
    """Query LiveKit for explicit dispatches associated with a room.

    Used for verification and diagnostic tests.
    """
    lkapi = get_livekit_api()
    try:
        # Calls livekit-api agent_dispatch.list_dispatch to retrieve all registered dispatches for the room
        dispatches = await asyncio.wait_for(lkapi.agent_dispatch.list_dispatch(room_name), timeout=5.0)
        return [
            {
                "id": d.id,
                "agent_name": d.agent_name,
                "room": d.room,
                "metadata": d.metadata,
                "state": d.state,
            }
            for d in dispatches
        ]
    finally:
        await lkapi.aclose()


def create_operator_token(room_name: str, call_id: str) -> str:
    """Mint a restricted, room-scoped AccessToken for an operator/supervisor.

    GRANTS:
    - identity: f"operator-{call_id[:8]}"
    - hidden=True: SFU does not broadcast the operator to other participants
    - can_subscribe=True: Operator can subscribe to audio tracks (customer + agent)
    - can_publish=False: Operator cannot publish audio/video tracks
    - can_publish_data=True: Operator can send whisper data packets to agent
    - room: Scoped strictly to room_name
    """
    identity = f"operator-{call_id[:8]}"
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Supervisor")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                hidden=True,
                can_subscribe=True,
                can_publish=False,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )
    return token

