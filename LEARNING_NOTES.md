# Learning Notes

## [2026-10-20] - Phase 1: Self-Hosted LiveKit Server Setup

### (a) What Was Built
- Configured and deployed a self-hosted LiveKit server locally on macOS (Apple Silicon) via Docker Compose using pinned image `livekit/livekit-server:v1.13.10`.
- Configured [`infra/livekit.yaml`](file:///Users/shivamsingh/development/project/voice-agent/infra/livekit.yaml) with explicit port mappings: HTTP/WebSocket signalling on `7880/tcp`, ICE-over-TCP fallback on `7881/tcp`, and single-port UDP muxing on `7882/udp`.
- Solved macOS Docker bridge reachability by setting `rtc.use_external_ip: false` and `rtc.node_ip: 127.0.0.1`.
- Configured [`infra/docker-compose.yml`](file:///Users/shivamsingh/development/project/voice-agent/infra/docker-compose.yml) to inject API credentials dynamically via `LIVEKIT_KEYS: "${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}"` using repo-root `.env` interpolation, keeping secrets out of version control and isolating application provider keys from the LiveKit container.
- Created and executed [`infra/scripts/test_livekit_connection.py`](file:///Users/shivamsingh/development/project/voice-agent/infra/scripts/test_livekit_connection.py), verifying RoomService room creation, room listing, and participant `AccessToken` (JWT) generation.

### (b) How the Underlying LiveKit / WebRTC Mechanism Works
- **Role of the SFU (Selective Forwarding Unit)**:
  - Unlike Peer-to-Peer Mesh (where each client sends media to every other peer, scaling as $O(N^2)$ bandwidth) or MCU (Multipoint Control Unit, which transcodes and composites all audio/video on the server, adding 100-300ms CPU latency), LiveKit operates as an SFU.
  - Each participant publishes their media track once to the server. The SFU intelligently inspects RTP packets and forwards them directly to subscribed participants without decoding or re-encoding. This introduces negligible latency (< 5ms) and preserves conversational real-time characteristics.
- **Two Distinct Network Planes**:
  - **Signalling Plane (TCP 7880)**: An initial HTTP handshake upgrades to a persistent WebSocket connection. It exchanges SDP (Session Description Protocol) offer/answers, room state updates, participant join/leave events, and trickled ICE candidates.
  - **Media Plane (UDP 7882)**: Once ICE negotiation succeeds, encrypted SRTP (Secure Real-time Transport Protocol) audio packets and SRTCP control packets flow directly over UDP.
- **ICE Candidate Types**:
  - `host`: The physical or virtual network interface address directly attached to the machine (in our local case, `127.0.0.1`).
  - `srflx` (Server Reflexive): The public IP address and port allocated by an external STUN (Session Traversal Utilities for NAT) server to traverse symmetric NATs.
  - `relay`: A relay server endpoint allocated via TURN (Traversal Using Relays around NAT) when direct UDP/TCP communication is blocked by restrictive firewalls.
- **Why UDP is Essential for Real-Time Conversational Audio**:
  - TCP guarantees in-order, lossless packet delivery via acknowledgments and automatic retransmission. When a packet drops in a TCP stream, "head-of-line blocking" prevents all subsequent packets from being processed until the missing packet is retransmitted, resulting in audible lag spikes (jitter spikes of 300–1000ms).
  - In conversational AI, delayed audio is useless—it is better to drop a late 20ms audio frame than to wait for a retransmission and interrupt conversational flow. UDP delivers datagrams with minimal overhead and zero retransmission delays. The Opus audio codec uses packet loss concealment (PLC) and jitter buffers to smoothly bridge occasional dropped UDP packets while maintaining latency under 200ms.

### (c) Files & Functions to Read First
- [`infra/livekit.yaml`](file:///Users/shivamsingh/development/project/voice-agent/infra/livekit.yaml): Inspect the `rtc` block (`tcp_port`, `udp_port`, `use_external_ip`, `node_ip`) to understand WebRTC transport binding.
- [`infra/docker-compose.yml`](file:///Users/shivamsingh/development/project/voice-agent/infra/docker-compose.yml): Review the port mappings and `LIVEKIT_KEYS` environment injection.
- [`infra/scripts/test_livekit_connection.py`](file:///Users/shivamsingh/development/project/voice-agent/infra/scripts/test_livekit_connection.py):
  - `api.LiveKitAPI(url, api_key, api_secret)`: Initializes the server API client.
  - `lkapi.room.create_room(api.CreateRoomRequest(...))`: Room lifecycle management.
  - `api.AccessToken(...).with_grants(...).to_jwt()`: Demonstrates how participant room tokens are constructed and signed.

### (d) Reviewer Questions & Short Answers
1. **Why did we configure UDP port muxing (`rtc.udp_port: 7882`) on macOS instead of the standard 50000-60000 range?**
   - *Answer*: Docker Desktop on macOS does not support native Linux host networking; containers run inside a virtualized hypervisor VM. Publishing a 10,000-port range (`50000-60000`) forces Docker Desktop to create thousands of proxy forwards, severely degrading container startup, exhausting host file descriptors, and causing stability issues. Single-port UDP muxing funnels all RTP media through port `7882/udp`, ensuring efficient and reliable local Docker operation.
2. **Why was setting `rtc.node_ip: 127.0.0.1` and `rtc.use_external_ip: false` critical for local development?**
   - *Answer*: Without explicit configuration, LiveKit inside Docker advertises its container bridge network IP (e.g., `172.18.0.2`), which is completely unroutable from the macOS host browser. Conversely, enabling `use_external_ip` discovers the host's public WAN IP via STUN, which fails when routers lack NAT loopback (hairpinning). Forcing `use_external_ip: false` and `node_ip: 127.0.0.1` ensures LiveKit advertises `127.0.0.1`, which maps directly to Docker Desktop's port forwards on the Mac.
3. **How does LiveKit validate room join tokens without querying an external database?**
   - *Answer*: LiveKit access tokens are self-contained HMAC-SHA256 signed JSON Web Tokens (JWTs). The API server signs the token using the `LIVEKIT_API_SECRET`. When a client initiates a WebSocket connection on port 7880, the LiveKit server decodes the token, checks the signature against its configured `LIVEKIT_KEYS`, validates the expiry timestamp, and checks the embedded video grants (such as `room_join: true` and `room: "room_name"`). If the signature and grants match, connection is admitted immediately with zero database roundtrips.

### (e) Decisions & Tradeoffs
- **Pinned Image `livekit/livekit-server:v1.13.10`**: Pinned to a specific verified stable tag rather than `latest` to prevent unexpected API or config deprecation issues during development.
- **Key Injection via `--env-file .env -f infra/docker-compose.yml`**: Keys are read from the root `.env` during compose interpolation, while keeping `.env_file` off the service definition to prevent exposing third-party provider keys (Deepgram, OpenAI, etc.) to the LiveKit container.
- **Local Loopback Candidate vs Public STUN**: Chosen for local developer ergonomics, guaranteeing reproducible offline capability.

### (f) Known Gaps
- **Host-Bound Local Agent**: `node_ip=127.0.0.1` only works while the agent worker and browser run on the host; an agent running inside a Docker container would need a different address strategy (such as container networking or `host.docker.internal`).
- **Single UDP Mux Port Scale Limit**: Adequate for development and small tests, but high-concurrency production requires multi-port ranges (`50000-60000`) across multiple CPU cores.
- **No Embedded TURN / TLS Locally**: Embedded TURN server (TLS 443 / UDP 3478) and TLS reverse proxy are omitted for local simplicity, but required in production to navigate enterprise firewalls.

---

## [2026-10-20] - Phase 2: FastAPI Backend + SQLite Storage

### (a) What Was Built
- Initialized a standalone `uv` project in `api/` pinned to Python `3.12.12`.
- Pinned and installed dependencies:
  - `fastapi==0.151.0`
  - `uvicorn==0.54.0`
  - `livekit-api==1.2.2`
  - `python-dotenv==1.2.4`
  - `pytest==9.1.1`
  - `httpx==0.28.1`
- Built SQLite storage abstraction in [`api/app/storage.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/storage.py) using Python stdlib `sqlite3` (parameterized queries, WAL mode, foreign keys, no ORM). Tables: `agents`, `calls`, `transcript_lines`.
- Built LiveKit interaction service in [`api/app/livekit_service.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/livekit_service.py) handling `CreateRoomRequest(empty_timeout=60)`, `CreateAgentDispatchRequest(agent_name="support-agent")`, and restricted customer `AccessToken` issuance with retry/timeout error handling.
- Built FastAPI application in [`api/app/main.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/main.py):
  - Public endpoints: `POST /agents`, `GET /agents`, `GET /agents/{id}`, `POST /calls`, `GET /calls/{id}` (transcript gated until `ended`), `GET /calls?status=active`.
  - Internal endpoints: `POST /internal/calls/{id}/started`, `POST /internal/calls/{id}/transcript` (idempotent upsert), `POST /internal/calls/{id}/ended` protected by `X-Internal-Token` and verified via `secrets.compare_digest`.
- Created demo seed script in [`api/seed.py`](file:///Users/shivamsingh/development/project/voice-agent/api/seed.py) provisioning "Acme Telecom Support" with recording disclosure instructions.
- Automated test suite in [`api/tests/test_api.py`](file:///Users/shivamsingh/development/project/voice-agent/api/tests/test_api.py) using sync `TestClient` and temporary SQLite database (4 test suites, 100% pass).

### (b) How the Underlying LiveKit / WebRTC Mechanism Works
- **AccessToken JWT Claims & Grants**:
  - LiveKit tokens encode permission grants into the `video` claim of an HMAC-SHA256 signed JWT.
  - In our implementation, customer tokens are issued with:
    - `identity`: `customer-<call_id>` (guarantees participant uniqueness).
    - `roomJoin: true` and `room: <room_name>` (strictly confines connection to that room; the token is cryptographically rejected if presented to any other room).
    - `canPublish: true` and `canPublishSources: ["microphone"]` (enforces audio-only publishing and rejects camera video tracks).
    - `canPublishData: false` (strictly forbids publishing to LiveKit data channels, preventing customers from injecting spoofed supervisor whispers or arbitrary control packets).
    - `canSubscribe: true` (permits receiving the audio track published by the voice agent).
- **Explicit Dispatch vs. Token-Based Dispatch**:
  - In standard *automatic/token dispatch*, any worker listening to LiveKit takes jobs based on generic room join events.
  - In *explicit dispatch* (`lkapi.agent_dispatch.create_dispatch`), the API explicitly instructs LiveKit to dispatch a job targeting a specific registered agent pool (`agent_name="support-agent"`) for a specific room.
  - Crucially, explicit dispatch attaches custom JSON `metadata: {"call_id": ..., "agent_id": ...}` directly to the job assignment. The worker receives this assignment on startup and immediately knows which database records and prompt instructions apply, avoiding extra discovery roundtrips.
- **How a Worker Registers and Receives a Job (from LiveKit Agents Documentation)**:
  - A Python LiveKit worker process establishes a WebSocket connection to LiveKit Server's internal agent RPC service (`/agent`).
  - The worker announces its presence with a registered `agent_name` (e.g. `support-agent`).
  - When the API calls `create_dispatch(agent_name="support-agent", room=...)`, the LiveKit SFU matches the dispatch request to an idle worker registered under that name.
  - The SFU delivers a `JobAssignment` containing the room token and the dispatch `metadata`. The worker accepts the job, joins the room as an agent participant, and begins audio streaming.
  - *(Note: Worker registration and job lifecycle will be empirically verified in Phase 3)*.

### (c) Files & Functions to Read First
- [`api/app/storage.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/storage.py): Review schema definitions and `set_call_ended()` (the single hook for post-call compliance).
- [`api/app/livekit_service.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/livekit_service.py): Review `create_room_and_dispatch()` for `empty_timeout=60` rationale and customer token restrictions.
- [`api/app/main.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/main.py): Inspect public call initialization, the transcript privacy gate in `get_call()`, and `verify_internal_token()`.
- [`api/seed.py`](file:///Users/shivamsingh/development/project/voice-agent/api/seed.py): Review the demo system prompt and model/voice definitions.

### (d) Reviewer Questions & Short Answers
1. **Why is `empty_timeout` set to 60 seconds on room creation?**
   - *Answer*: If a user invokes `POST /calls` but never opens their browser to join, or leaves immediately after joining, LiveKit would keep the room and its allocated WebRTC UDP mux resources open indefinitely. Setting `empty_timeout=60` instructs the SFU to automatically close and destroy the room after 60 seconds of vacancy.
2. **Why is `can_publish_data` set to `False` on the customer token?**
   - *Answer*: LiveKit data channels are used for supervisory control messages, including operator whispers delivered to the voice agent. Restricting `can_publish_data: false` at the cryptographic token level prevents the client browser from publishing data messages, guaranteeing customers cannot spoof operator whispers or tamper with agent state.
3. **Why are internal endpoints protected by `secrets.compare_digest`?**
   - *Answer*: Standard string comparison (`==`) short-circuits on the first mismatched character, creating subtle differences in execution time depending on how many leading characters match. An attacker can exploit this via timing analysis to brute-force secret keys over network requests. `secrets.compare_digest` executes in constant time regardless of character matches, preventing timing side-channel attacks.

### (e) Decisions & Tradeoffs
- **Stdlib `sqlite3` over ORMs**: Avoided SQLAlchemy/SQLModel to keep code simple, readable, and free of unnecessary dependencies for the POC. Queries are explicitly parameterized and use WAL mode for concurrency.
- **Blocking SQLite in Async Handlers**: SQLite queries execute synchronously inside async route handlers. While inappropriate for heavy enterprise loads, this is an accepted POC tradeoff that eliminates the need for async database drivers or threadpools.
- **Explicit Dispatch over Implicit Room Discovery**: Guarantees deterministic job targeting with metadata attached.

### (f) Known Gaps
- **Public Endpoints Lack Authentication**: `/agents` and `/calls` endpoints are unauthenticated in this POC.
- **Abandoned Calls Remain in "created" in Database**: When LiveKit destroys an empty room after 60 seconds, the SQLite database row remains in `created` status because no webhook or polling mechanism syncs closed rooms back to SQLite in Phase 2.
- **Seed Model and Voice IDs are UNVERIFIED**:
  - Model: `llama-3.3-70b-versatile` (from [Groq Model Docs](https://console.groq.com/docs/models)).
  - Voice: `aura-asteria-en` (from [Deepgram Aura Docs](https://developers.deepgram.com/docs/aura)).
  - Both IDs are UNVERIFIED in the livekit-plugins runtime and must be re-checked against installed plugin versions in Phase 3.
