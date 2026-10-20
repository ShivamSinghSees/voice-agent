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

            # 1. Create Room with empty_timeout=60
            # WHY empty_timeout=60: If a customer triggers call creation but closes the tab without joining,
            # or leaves prematurely, LiveKit automatically cleans up and terminates the room after 60 seconds
            # rather than leaving orphaned rooms and allocated media ports lingering on the SFU.
            room_req = api.CreateRoomRequest(
                name=room_name,
                empty_timeout=60,
                max_participants=5,
            )
            # Timeout guard of 5.0 seconds prevents blocking backend event loop if LiveKit is down
            await asyncio.wait_for(lkapi.room.create_room(room_req), timeout=5.0)

            # 2. Create Explicit Agent Dispatch
            # WHY agent_name=config.AGENT_NAME: Explicit dispatch targets the specific registered worker pool
            # ('support-agent') instead of relying on default auto-dispatch, fulfilling assignment requirements.
            # WHY metadata: Serializes call_id and agent_id directly into the job dispatch payload,
            # allowing the worker in Phase 3 to load the exact agent system prompt from DB without extra RPCs.
            dispatch_req = api.CreateAgentDispatchRequest(
                agent_name=config.AGENT_NAME,
                room=room_name,
                metadata=json.dumps({"call_id": call_id, "agent_id": agent_id}),
            )
            await asyncio.wait_for(lkapi.agent_dispatch.create_dispatch(dispatch_req), timeout=5.0)

            # 3. Mint Customer AccessToken
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
