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
