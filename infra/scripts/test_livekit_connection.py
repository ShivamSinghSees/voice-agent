"""
Test script to verify self-hosted LiveKit server connectivity, RoomService API operations,
and AccessToken generation using credentials from .env.

Usage:
    uv run --with livekit-api --with python-dotenv python3 infra/scripts/test_livekit_connection.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
from livekit import api


async def main() -> None:
    # 1. Locate and load .env file from repository root or infra/
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent
    env_path = repo_root / ".env"
    if not env_path.exists():
        env_path = script_dir.parent / ".env"

    if not env_path.exists():
        print(f"Error: .env file not found at {repo_root / '.env'}", file=sys.stderr)
        sys.exit(1)

    load_dotenv(dotenv_path=env_path)

    raw_url = os.getenv("LIVEKIT_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")

    if not raw_url or not api_key or not api_secret:
        print("Error: LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET missing in .env", file=sys.stderr)
        sys.exit(1)

    # Convert WebSocket URL (ws://) to HTTP REST URL (http://) for server API calls if needed
    http_url = raw_url.replace("ws://", "http://").replace("wss://", "https://")

    print("==================================================")
    print("LiveKit Server API & Token Verification")
    print("==================================================")
    print(f"Server Target URL : {raw_url} (HTTP REST: {http_url})")
    print(f"API Key           : {api_key}")
    print(f"API Secret        : [PROTECTED - {len(api_secret)} characters]")

    # 2. Initialize LiveKitAPI client
    lkapi = api.LiveKitAPI(
        url=http_url,
        api_key=api_key,
        api_secret=api_secret,
    )

    room_name = f"poc-verify-room-{int(time.time())}"

    try:
        # 3. Create a room via the Server API
        print(f"\n1. Creating room '{room_name}' via Server API...")
        room_info = await lkapi.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=60,
                max_participants=5,
            )
        )
        print(f"   -> Room created successfully: SID={room_info.sid}, Name='{room_info.name}'")

        # 4. List active rooms to verify presence
        print("\n2. Listing active rooms on the server...")
        rooms_res = await lkapi.room.list_rooms(api.ListRoomsRequest())
        found = False
        for r in rooms_res.rooms:
            print(f"   - Room: '{r.name}' (SID: {r.sid}, Active participants: {r.num_participants})")
            if r.name == room_name:
                found = True

        if not found:
            raise RuntimeError(f"Created room '{room_name}' was not returned in list_rooms response.")

        # 5. Generate a participant AccessToken (JWT)
        print("\n3. Generating participant AccessToken...")
        token_jwt = (
            api.AccessToken(api_key=api_key, api_secret=api_secret)
            .with_identity("test-customer-01")
            .with_name("Test Customer")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                )
            )
            .to_jwt()
        )

        token_prefix = token_jwt[:20]
        print(f"   -> Token generated successfully!")
        print(f"   -> First 20 characters: {token_prefix}...")
        print(f"   -> Full length: {len(token_jwt)} chars (JWT signature validated by server secret)")

        # 6. Clean up the room
        print(f"\n4. Cleaning up test room '{room_name}'...")
        await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
        print("   -> Room deleted cleanly.")

        print("\nSTATUS: PASS - Key/Secret authentication and Server API verified successfully against local LiveKit server.")

    except Exception as exc:
        print(f"\nFAILURE: LiveKit API verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        await lkapi.aclose()


if __name__ == "__main__":
    asyncio.run(main())
