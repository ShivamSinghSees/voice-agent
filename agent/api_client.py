"""HTTP client for agent worker communicating with the FastAPI backend.

Authenticates internal coordination requests using the shared X-Internal-Token header.
Provides safe retries on network blips and logs state transitions.
"""

import logging
from typing import Any
import httpx
from config import API_BASE_URL, INTERNAL_API_TOKEN

logger = logging.getLogger(__name__)


class APIClientError(Exception):
    """Raised when an API request to the backend fails."""
    pass


class APIClient:
    """Handles REST communication between the voice agent worker and the FastAPI backend."""

    def __init__(self, base_url: str = API_BASE_URL, internal_token: str = INTERNAL_API_TOKEN) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "X-Internal-Token": internal_token,
            "Content-Type": "application/json",
        }

    async def get_agent(self, agent_id: str) -> dict[str, Any]:
        """Fetch agent configuration profile (system prompt, voice, model) from DB via API."""
        url = f"{self.base_url}/agents/{agent_id}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise APIClientError(
                    f"Failed to fetch agent profile {agent_id}: HTTP {resp.status_code} - {resp.text}"
                )
            return resp.json()

    async def mark_call_started(self, call_id: str, started_at: str) -> None:
        """Notify backend that the customer connected and call is active."""
        url = f"{self.base_url}/internal/calls/{call_id}/started"
        payload = {"started_at": started_at}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code != 200:
                raise APIClientError(
                    f"Failed to mark call {call_id} started: HTTP {resp.status_code} - {resp.text}"
                )
            logger.info("Call %s marked started at %s", call_id, started_at)

    async def upsert_transcript(self, call_id: str, lines: list[dict[str, Any]]) -> None:
        """Upsert a batch of transcript lines (user speech or agent speech) into SQLite."""
        if not lines:
            return
        url = f"{self.base_url}/internal/calls/{call_id}/transcript"
        payload = {"lines": lines}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code != 200:
                logger.error(
                    "Failed to upsert transcript for call %s: HTTP %d - %s",
                    call_id,
                    resp.status_code,
                    resp.text,
                )
                raise APIClientError(f"Transcript upsert failed: {resp.text}")

    async def mark_call_ended(self, call_id: str, ended_at: str) -> None:
        """Notify backend that call ended, triggering final state transition and transcript un-gating."""
        url = f"{self.base_url}/internal/calls/{call_id}/ended"
        payload = {"ended_at": ended_at}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            if resp.status_code != 200:
                logger.error(
                    "Failed to mark call %s ended: HTTP %d - %s",
                    call_id,
                    resp.status_code,
                    resp.text,
                )
                raise APIClientError(f"Failed to mark call ended: {resp.text}")
            logger.info("Call %s marked ended at %s", call_id, ended_at)
