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

---

## Phase 3: LiveKit Agent Worker & Task 0 Bug Fix

### (a) What Was Built
1. **Task 0 Bug Fix in `api/`**:
   - Diagnosed and resolved the duplicate dispatch bug in [`api/app/livekit_service.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/livekit_service.py).
   - Root cause: `CreateRoomRequest` was originally called without `agents`. LiveKit server automatically created a default unassigned dispatch (`agent_name: ""`). Calling `create_dispatch` immediately after created the second dispatch.
   - Updated `create_room_and_dispatch` to configure `RoomAgentDispatch(agent_name="support-agent")` atomically inside `CreateRoomRequest.agents`.
   - Removed defensive dead code (`list_dispatch` and cleanup loop) since atomic creation prevents stray dispatches entirely.
   - Added unit test `test_create_room_and_dispatch_deduplication` in [`api/tests/test_api.py`](file:///Users/shivamsingh/development/project/voice-agent/api/tests/test_api.py). Verified live: creates **exactly 1 dispatch** for `support-agent`.
   - Added dedicated tests for 401 internal token, transcript upsert idempotency, and transcript privacy gating until call completion.
2. **Agent Worker uv Project (`agent/`)**:
   - Initialized Python 3.12 project with `[tool.uv] package = false`.
   - Pinned and installed dependencies: `livekit-agents==1.8.8`, `livekit-plugins-deepgram==1.8.8`, `livekit-plugins-groq==1.8.8`, `livekit-plugins-silero==1.8.8`, `livekit-plugins-turn-detector==1.8.8`, `httpx==0.28.1`, `python-dotenv==1.1.1`, and `pytest==9.1.1`.
   - Pre-downloaded neural assets for turn-detector and Silero VAD via `python -m livekit.agents download-files`.
3. **Core Modules in `agent/`**:
   - [`agent/config.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/config.py): Resolves root `.env` by explicit absolute path.
   - [`agent/api_client.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/api_client.py): Async HTTP client with `X-Internal-Token` header calling backend endpoints (`get_agent`, `mark_call_started`, `upsert_transcript`, `mark_call_ended`).
   - [`agent/tracking_llm.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tracking_llm.py): Transparent proxy wrapping Groq LLM to record the complete text generated by the model per turn for barge-in comparison.
   - [`agent/agent.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/agent.py): Full voice worker registering as `support-agent` with the framework's default process-per-job executor (`JobExecutorType.PROCESS`).
     - Handles race condition by checking `room.remote_participants` first.
     - Logs both customer server-side `joined_at` and worker detection time, and transmits real join time to `POST /internal/calls/{id}/started`.
     - Links session strictly to customer (`RoomOptions(participant_identity=customer_participant.identity, close_on_disconnect=True)`).
     - Captures playback-accurate transcript timing: agent lines use `started_speaking_at` (recorded when first audio frame plays out via WebRTC) and `stopped_speaking_at` with millisecond precision ISO-8601 timestamps.
     - Logs debug message on every barge-in comparing FULL LLM generated text vs KEPT text in transcript.
     - Delivers initial greeting with recording disclosure.
     - Streams transcript lines to API and finalizes call on customer disconnect.
4. **Verification & Test Suites**:
   - [`agent/tests/test_joined_at.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tests/test_joined_at.py): Unit tests verifying SDK `joined_at` millisecond-to-datetime conversion.
   - [`agent/tests/test_transcript_truncation.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tests/test_transcript_truncation.py): Validates truncation state on interrupted turns, TrackingLLM recording, and barge-in debug logging.
   - [`agent/tests/test_pipeline_text.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tests/test_pipeline_text.py): Exercises real Groq API text generation with verified model `qwen/qwen3.8-27b`.
   - [`agent/scripts/test_race_condition.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/scripts/test_race_condition.py): End-to-end live test verifying customer pre-join, server-side join timestamp propagation, RoomService published audio proof, real customer speech via STT, and final call JSON.

---

### (b) Plain-Language Explanation of Underlying LiveKit Mechanisms

1. **Worker Registration & Discovery**:
   - When the agent worker starts, it establishes a WebSocket connection to the self-hosted LiveKit server signalling port (`ws://localhost:7880`).
   - It sends a registration request declaring its worker capabilities and registered agent name (`support-agent`). The server logs `registered worker` with a unique worker ID (e.g. `AW_...`).
2. **Explicit Dispatch**:
   - When the FastAPI backend receives `POST /calls`, it creates the room with `RoomAgentDispatch(agent_name="support-agent", metadata=...)` embedded in `CreateRoomRequest`.
   - The LiveKit server matches the room to an idle worker registered for `support-agent` and dispatches a `JobRequest`.
   - The worker receives `ctx.job`, parses the serialized metadata (`call_id`, `agent_id`), fetches the agent's prompt, voice, and model from the backend API, and joins the WebRTC room via `ctx.connect()`.
3. **AgentSession Pipeline**:
   - An `AgentSession` coordinates five components: Deepgram streaming STT (`nova-3`), Groq LLM (`qwen/qwen3.8-27b`), Deepgram streaming TTS (`aura-asteria-en`), Silero VAD, and LiveKit Turn Detector.
   - When the customer speaks, Silero VAD detects voice activity and Deepgram STT transcribes the audio stream.
   - When the turn detector signals end-of-turn, the customer transcript is sent to Groq LLM.
   - As Groq streams text tokens, they are piped directly into Deepgram TTS, which streams linear16 PCM audio frames.
   - When the first audio frame reaches the WebRTC playout buffer, `_on_first_frame` records `started_speaking_at`. When playout completes, `stopped_speaking_at` is recorded.
4. **Interruption & Truncation Internals in `livekit-agents` 1.8.8**:
   - If the customer speaks while the agent is playing audio, Silero VAD fires and sets `speech_handle.interrupted = True`.
   - In `livekit/agents/voice/agent_activity.py` (lines 3330–3356), audio output buffers are cleared immediately and `playback_ev = await audio_output.wait_for_playout()` retrieves the audio playback position.
   - `TranscriptSynchronizer` (`livekit/agents/voice/transcription/synchronizer.py` lines 411–430) paces spoken words against elapsed playback time.
   - At lines 4658–4681, because `entry.out.played == "partial"`, `_create_assistant_message` uses `forwarded_text` (the partial synchronized transcript) rather than the original LLM text.
   - **Exact vs. Estimated for Deepgram TTS**:
     - In `livekit-plugins-deepgram/tts.py` lines 366–425, Deepgram WebSocket TTS emits raw binary linear16 PCM without word timestamps.
     - **EXACT**: The audio playout elapsed time (`playback_position`) and which audio samples were sent to the WebRTC buffer.
     - **ESTIMATED**: The mapping from elapsed audio time to spoken words. Because Deepgram sends no word boundary events, LiveKit estimates word boundaries by running `SpeakingRateDetector` (estimating syllables per second on the audio stream) and hyphenating words. The text kept in chat history is therefore an **ESTIMATED** truncation based on speaking-rate pacing, not exact word timestamps from Deepgram.

---

### (c) Files & Functions to Read First
- [`agent/agent.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/agent.py): Read `entrypoint(ctx: JobContext)` to see the complete lifecycle: metadata parsing, customer discovery in `room.remote_participants`, server-side join time extraction, session creation, playback-accurate transcript timing (`started_speaking_at`), barge-in debug logging, and disconnect handling.
- [`agent/tracking_llm.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tracking_llm.py): Inspect `TrackingLLM.chat` to see how full LLM stream content is retained across chunk deltas without interfering with audio generation.
- [`agent/scripts/test_race_condition.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/scripts/test_race_condition.py): Review `run_race_test()` for the automated test orchestrating customer pre-join, late worker startup, RoomService published audio proof, real customer speech via STT, and final call JSON validation.
- [`agent/tests/test_joined_at.py`](file:///Users/shivamsingh/development/project/voice-agent/agent/tests/test_joined_at.py): Review unit tests for participant `joined_at` millisecond-to-datetime conversion.
- [`api/app/livekit_service.py`](file:///Users/shivamsingh/development/project/voice-agent/api/app/livekit_service.py): Read `create_room_and_dispatch` to see atomic dispatch provisioning in `CreateRoomRequest.agents`.

---

### (d) Reviewer Questions & Short Answers

1. **How does barge-in truncate agent speech in LiveKit Agents, and why is Deepgram TTS truncation estimated rather than exact?**
   - *Answer*: When Silero VAD detects user speech while the agent is speaking, `speech_handle.interrupted` is flagged. `AgentActivity` stops audio playout, flushes output buffers, and retrieves `playback_ev.synchronized_transcript` from `TranscriptSynchronizer` (`agent_activity.py` lines 3330–3356). Because Deepgram WebSocket TTS sends raw binary PCM chunks without word alignment metadata (`livekit-plugins-deepgram/tts.py`), LiveKit uses `SpeakingRateDetector` to estimate syllables per second on the audio stream and hyphenates text to estimate how many words played before interruption. Therefore, playout time is exact, but the text boundary is an estimate.

2. **How does the worker execute concurrent jobs, and why is process-per-job used instead of thread mode?**
   - *Answer*: The worker relies on the LiveKit framework's default process-per-job executor (`JobExecutorType.PROCESS`). Each incoming job is assigned to an isolated child process spawned by the worker supervisor. This provides strict process isolation: a crash, unhandled segfault in native audio/C/Rust extensions, or CPU-blocking operation in one call session cannot affect other concurrent sessions or crash the supervisor worker.
   - *Clarification on the earlier error*: An earlier test run encountered `AttributeError: Can't get attribute 'entry' on <module '__main__'>` with `File "<string>"`. That was an artifact of running the worker entrypoint dynamically via inline string execution (`python -c ...`), which prevents Python's multiprocessing `spawn` method from re-importing the `__main__` entrypoint. When executed normally from disk (`python agent.py dev` or `python agent.py start`), `multiprocessing.spawn` cleanly loads the module and spawns isolated child processes without pickling issues. Thread mode is therefore neither necessary nor used.

3. **Why must the worker inspect `room.remote_participants` rather than only awaiting `participant_connected`?**
   - *Answer*: If the worker relied only on the `participant_connected` event, it would deadlock if the customer joined slightly before the worker finished connecting to the room (since `participant_connected` only fires for participants who join *after* the local participant). After calling `await ctx.connect()`, the worker inspects `ctx.room.remote_participants` for any participant with identity prefix `customer-`. If present, it binds to that participant immediately; only if absent does it await the `participant_connected` event.

---

### (e) Decisions & Tradeoffs
- **Model Selection (`qwen/qwen3.8-27b`)**:
  - Live Groq API benchmark across available models revealed `qwen/qwen3.8-27b` delivered an average Time-to-First-Token (TTFT) of **182.0ms** (compared to 811.7ms for `openai/gpt-oss-20b` and 613.0ms for `openai/gpt-oss-120b`).
  - `openai/gpt-oss-20b` generates reasoning tokens in `delta.reasoning` before producing content, introducing ~600ms of reasoning delay. While LiveKit's `inference/llm.py` discards `delta.reasoning` so reasoning never reaches TTS, the latency penalty remains.
  - `qwen/qwen3.8-27b` generates direct conversational speech without reasoning overhead, adheres strictly to system instructions (recording disclosure and polite refusal of refund promises), and avoids markdown formatting. Seed and database were updated accordingly.
  - Note: `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` returned `HTTP 404 - {"error":{"code":"model_not_found"}}` on the active Groq account key.
- **Process-Per-Job Isolation (`JobExecutorType.PROCESS`)**:
  - The worker uses the LiveKit default process-per-job executor (`JobExecutorType.PROCESS`). Each job is executed in an isolated child process (`pid`), ensuring memory and fault isolation across concurrent calls.
- **Atomic Room Dispatch over Two-Step Creation**:
  - Embedding `RoomAgentDispatch` directly inside `CreateRoomRequest.agents` creates the room and dispatch atomically, preventing duplicate empty dispatches and removing defensive polling loops.
- **Millisecond Timestamp Precision**:
  - Formatted all timestamps as UTC ISO-8601 with millisecond precision (`%Y-%m-%dT%H:%M:%S.sssZ`) to preserve accurate sub-second turn offsets and join-detection latency.
- **Custom `TrackingLLM` over Span Inspection**:
  - Transparently captures complete LLM generated text across streaming deltas, enabling deterministic programmatic comparison between full generated text and kept truncated text on barge-in.

---

### (f) Known Gaps
- **Audio Background Voice Cancellation (BVC)**: Not enabled on local self-hosted LiveKit (LiveKit Cloud proprietary feature).
- **Web UI & Operator Whisper View**: Customer web client implemented in Phase 4; operator listen/whisper interfaces deferred to Phase 5.

---

## Phase 4: Customer Web Client (React + Vite + livekit-client)

### (a) What Was Built
1. **Frontend Architecture in `web/`**:
   - React 18 + Vite + TypeScript application with pinned `livekit-client: 2.23.0` and `react-router-dom: ^6.28.0`.
   - Routing structure separating the customer experience from operator tooling:
     - `/`: [`CustomerCallPage.tsx`](file:///Users/shivamsingh/development/project/voice-agent/web/src/pages/CustomerCallPage.tsx) (active customer voice interface).
     - `/operator`: [`OperatorPlaceholderPage.tsx`](file:///Users/shivamsingh/development/project/voice-agent/web/src/pages/OperatorPlaceholderPage.tsx) (Phase 5 placeholder).
2. **Customer Call Lifecycle & Controls**:
   - Dynamic agent picker fetching live profiles from `GET /agents` and rendering model, voice, and system prompt dynamically.
   - User gesture-driven Call button calling `POST /calls` once, initializing the LiveKit `Room`, and establishing WebRTC audio streaming.
   - Hang-up button that cleanly terminates the room session and triggers automatic polling of `GET /calls/{id}` until `status === "ended"`.
3. **Live Transcript via Native Text Streams**:
   - Registered `room.registerTextStreamHandler("lk.transcription", ...)` to consume incoming streaming transcriptions.
   - Identified speakers via sender identity (`customer-...` -> "You", agent identity -> "Agent").
   - Segment reconciliation using `lk.segment_id` for in-place text updates and finalization flags (`lk.transcription_final`).
   - Detailed chunk logging with `performance.now()` millisecond timestamps to the browser console for barge-in inspection.
4. **Agent State & Error Handling**:
   - Monitored real-time agent state transitions (`listening`, `thinking`, `speaking`) via `RoomEvent.ParticipantAttributesChanged` reading participant attribute `lk.agent.state`.
   - Comprehensive error alerts covering backend downtime (502 / network error), microphone permission rejection, connection failures, and 20-second remote agent join timeouts.
   - Persisted database transcript card rendered upon call completion proving backend sync.

---

### (b) How the Underlying LiveKit / WebRTC Mechanism Works (What Happens on "Call" Click)

1. **HTTP Initiation (`POST /calls`)**:
   - The user clicks "Call Agent". This click serves as the mandatory browser **user gesture** required by browser Autoplay policies to initialize an `AudioContext` and start audio playback.
   - The client issues `POST http://localhost:8000/calls` with `{ agent_id }`.
   - The FastAPI backend validates the agent, inserts a new call record in `voice_agent.db`, provisions a LiveKit room with atomic dispatch (`agent_name="support-agent"`), and mints a cryptographically signed customer JWT `AccessToken` containing room permissions (join + publish audio).
   - The API responds with `{ call_id, room_name, token, livekit_url: "ws://localhost:7880" }`.

2. **WebSocket Signalling (`ws://localhost:7880`)**:
   - The web client instantiates `new Room({ audioCaptureDefaults: { echoCancellation: true } })` and calls `await room.connect(livekit_url, token)`.
   - The client establishes an HTTP/WebSocket handshake to `ws://localhost:7880/rtc`.
   - The token is verified by LiveKit SFU using the shared `LIVEKIT_API_SECRET`.
   - LiveKit sends a protobuf `JoinResponse` containing room information, local participant SID, and server configuration.

3. **SDP Exchange & ICE Gathering**:
   - LiveKit uses dual peer connections: a **publisher peer connection** (client -> SFU) and a **subscriber peer connection** (SFU -> client).
   - LiveKit generates an SDP Offer over the WebSocket signalling channel.
   - The browser parses the offer, generates an SDP Answer, and begins ICE candidate gathering.
   - Local ICE candidates are gathered: in our self-hosted setup, the SFU advertises its host candidate at `127.0.0.1:7882` (UDP).
   - The browser sends STUN connectivity checks directly to `127.0.0.1:7882/udp`. Because both client and SFU run locally on macOS, the ICE agent nominates a direct `host <-> host` candidate pair without requiring STUN/TURN traversal.

4. **Microphone Track Publishing & Agent Track Subscription**:
   - `room.localParticipant.setMicrophoneEnabled(true)` invokes browser `navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })`.
   - The resulting `MediaStreamTrack` is added to the publisher peer connection. WebRTC encodes PCM audio frames (Opus codec, 48kHz) and streams UDP packets to `127.0.0.1:7882`.
   - Concurrently, the remote Python worker joins the room, creates an audio track for Deepgram TTS playback, and publishes it to the SFU.
   - The SFU routes the agent track down the subscriber peer connection. The client SDK receives `RoomEvent.TrackSubscribed`.
   - `track.attach(audioRef.current)` binds the incoming WebRTC `MediaStreamTrack` directly to the hidden `<audio autoPlay />` DOM element, streaming audio through the customer's headphones.

5. **Transcription Streaming over Reliable Data Channel**:
   - While audio flows over UDP media tracks, transcription flows over LiveKit's multiplexed WebRTC reliable data channel using the Data Streams sub-protocol.
   - The worker writes chunks to text stream topic `lk.transcription`. The client's `registerTextStreamHandler` receives chunks sequentially with zero packet loss, updating segment text in-place.

---

### (c) Files & Functions to Read First

- [`web/src/pages/CustomerCallPage.tsx`](file:///Users/shivamsingh/development/project/voice-agent/web/src/pages/CustomerCallPage.tsx):
  - `handleStartCall()`: Complete connection sequence, room event attachment, text stream registration, and audio playback.
  - `registerTextStreamHandler('lk.transcription', ...)`: Real-time text stream consumer with timestamped console logging and segment reconciliation.
  - `handleHangUp()` & `pollSavedCallTranscript()`: Clean disconnection and post-call persistence verification.
- [`web/src/services/api.ts`](file:///Users/shivamsingh/development/project/voice-agent/web/src/services/api.ts): Backend API integration (`fetchAgents`, `createCall`, `getCall`).
- [`web/src/types.ts`](file:///Users/shivamsingh/development/project/voice-agent/web/src/types.ts): Clean, beginner-readable TypeScript models.
- [`web/src/index.css`](file:///Users/shivamsingh/development/project/voice-agent/web/src/index.css): Plain, accessible CSS layout without bloated CSS frameworks or glassmorphic animations.

---

### (d) Reviewer Questions & Short Answers

1. **Why must audio playback and microphone capture be started from a button click gesture rather than a `useEffect` on page load?**
   - *Answer*: Modern web browsers enforce strict **Autoplay Policies**. Browsers block WebRTC audio playback and reject programmatic `getUserMedia` requests unless initiated by an explicit user gesture (such as clicking a "Call" button). Furthermore, running connection logic in React 18's `useEffect` under StrictMode causes double-mount cycles during development, creating duplicate room connections and orphaned dispatches. Starting strictly from `onClick` guarantees both autoplay compliance and exactly one room connection per user action.

2. **Why does the web client need a signed JWT access token instead of just connecting with the server API key and secret?**
   - *Answer*: Connecting directly with the API secret would require exposing `LIVEKIT_API_SECRET` in client-side JavaScript bundle files, compromising the entire SFU. The backend acts as an authorization gate: it authenticates/validates the caller, scopes permissions strictly to a specific room (`room_name`), restricts grants (e.g. `canPublish=True` for microphone audio, but `canPublishData=False` for whisper channels), sets short token expiry (e.g. 15 minutes), and stamps identity prefix `customer-...`.

3. **What is the difference between WebRTC media tracks and WebRTC data channels in LiveKit, and why is transcription sent over data streams?**
   - *Answer*: Audio tracks travel over SRTP (Secure Real-Time Transport Protocol) over UDP. They prioritize low latency over reliability: if a packet drops, WebRTC drops or conceals the sample rather than retransmitting, because delayed audio is useless in real-time conversation. In contrast, transcription text requires 100% transmission integrity: a lost token would corrupt words in the transcript. LiveKit uses SCTP (Stream Control Transmission Protocol) data channels over the DTLS connection, providing reliable, in-order text delivery with automatic retransmission while maintaining sub-50ms latency.

4. **Why is the selected ICE candidate pair not readable directly through public properties of `Room`, and how is it verified?**
   - *Answer*: `livekit-client` encapsulates the native browser `RTCPeerConnection` instances (`publisher` and `subscriber`) inside internal private classes (`RTCEngine` and `PCTransportManager`) to prevent client code from desynchronizing the transport state machine. Standard production inspection of ICE candidate pairs (confirming direct `host <-> host` binding on port `7882/udp`) is performed using Chrome's dedicated diagnostic interface at `chrome://webrtc-internals`.

---

### (e) Decisions & Tradeoffs

- **Plain `livekit-client` over `@livekit/components-react`**:
  - Pinned `livekit-client: 2.23.0`. Using the plain client SDK avoids wrapping components in opaque context providers and heavy pre-packaged CSS themes. Every line of room connection, track attachment, and text stream consumption is completely transparent and beginner-readable.
- **Strict React Event Architecture**:
  - Bound the call creation exclusively to `handleStartCall` on button click. Avoided any auto-connect side effects in `useEffect`, preventing React 18 StrictMode double-fetch bugs and duplicate backend dispatches.
- **In-Place Segment Updating (`lk.segment_id`)**:
  - Rather than treating each transcription message as a new appended chat bubble, the UI maps segments by `lk.segment_id`. Interim STT and TTS deltas update the active line in place and append an ellipsis (`...`) until `lk.transcription_final: "true"` commits the turn.
- **Headphone Isolation Guidance**:
  - Documented mandatory headphone usage during testing. In half-duplex or full-duplex conversational AI, acoustic feedback from unmuted laptop speakers into the microphone triggers the agent's VAD turn detector and causes the agent to interrupt itself.

---

### (f) Known Gaps

- **Operator Listen & Whisper View**: Scheduled for Phase 5.
- **Automated Browser Playwright Testing**: Playwright automated browser downloads are blocked by sandbox network restrictions; manual testing via local browser (`http://localhost:5173`) is required.
- **Audio Background Voice Cancellation (BVC)**: Client uses standard WebRTC `echoCancellation`; server-side deep learning Krisp noise cancellation requires LiveKit Cloud.
