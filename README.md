# Operator-Guided Voice AI Agent

*Work in progress*

Proof-of-concept operator-guided voice AI agent built with a self-hosted LiveKit server, Python LiveKit Agents worker, FastAPI backend, and React web client with customer and operator views.

---

## Setup

### Prerequisites
- macOS (Apple Silicon or Intel) or Linux
- Docker Desktop or Docker Engine with Docker Compose v2+
- Python 3.12 (managed via `uv`)
- Node.js 20+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package and environment manager)

### 1. Environment Configuration
Copy `.env.example` to `.env` and populate your credentials:
```bash
cp .env.example .env
```
Ensure `.env` contains:
```dotenv
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=your_generated_api_key
LIVEKIT_API_SECRET=your_generated_api_secret
DEEPGRAM_API_KEY=your_deepgram_api_key_here
GROQ_API_KEY=your_groq_api_key_here
DATABASE_PATH=./voice_agent.db
INTERNAL_API_TOKEN=your_secure_internal_token_here
CORS_ORIGINS=http://localhost:5173
```

### 2. Start Self-Hosted LiveKit Server
Run Docker Compose from the repository root:
```bash
# Ensure Docker CLI is in your PATH (e.g. export PATH="$HOME/.docker/bin:$PATH")
docker compose --env-file .env -f infra/docker-compose.yml up -d
```
> **Note**: Always invoke `docker compose` with `--env-file .env -f infra/docker-compose.yml` from the repository root so `${LIVEKIT_API_KEY}` and `${LIVEKIT_API_SECRET}` are interpolated correctly without leaking unrelated application secrets into the LiveKit container.

Verify the server logs:
```bash
docker compose --env-file .env -f infra/docker-compose.yml logs livekit
```

### 3. Verify Server API & Access Token Generation
Run the verification script using `uv`:
```bash
uv run --with livekit-api --with python-dotenv python3 infra/scripts/test_livekit_connection.py
```

### 4. Start FastAPI Backend Service
Navigate to the `api/` directory (or use `uv --directory api`):
```bash
# Start backend on http://localhost:8000
uv --directory api run uvicorn app.main:app --port 8000
```
On startup, SQLite tables are created automatically and the demo agent "Acme Telecom Support" is seeded.

### 5. Run Automated Backend Unit Tests
```bash
uv --directory api run pytest
```

### 6. Setup & Start Agent Worker
Pre-download the Silero VAD and turn detection neural models:
```bash
uv --directory agent run python -m livekit.agents download-files
```

Start the Python LiveKit Agent worker:
```bash
# Registers worker for explicit job dispatches matching agent_name="support-agent"
uv --directory agent run python agent.py start
```
*(Use `python agent.py dev` during development for auto-reload).*

### 8. Start Web Client (Customer UI)
```bash
# Install dependencies (pins livekit-client 2.23.0)
cd web && npm install

# Start Vite dev server on :5173
npm run dev
```

> **IMPORTANT: Browser Origin & CORS**:
> Always access the application at **`http://localhost:5173`** (do **not** use `http://127.0.0.1:5173`). The backend's default `CORS_ORIGINS` configuration is set to `http://localhost:5173`. Opening `127.0.0.1:5173` will trigger browser CORS preflight blocks.

> **IMPORTANT: Wear Headphones During Live Call & Barge-In Testing**:
> When testing live conversations and barge-in interruptions, **wear headphones**. If audio from the agent plays out through your computer speakers into your open microphone, the agent will detect its own speech as user speech and interrupt itself.

---

### Full System Run Order (4 Dedicated Terminals)
For end-to-end local operation, run each service in its own terminal window:

1. **Terminal 1 (LiveKit SFU Server)**:
   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml up
   ```
2. **Terminal 2 (FastAPI Backend Service)**:
   ```bash
   uv --directory api run uvicorn app.main:app --port 8000
   ```
3. **Terminal 3 (LiveKit Agent Voice Worker)**:
   ```bash
   uv --directory agent run python agent.py dev
   ```
4. **Terminal 4 (React + Vite Web Client)**:
   ```bash
   cd web && npm run dev
   ```
   Open **`http://localhost:5173`** in Chrome/Brave/Firefox.

---

## Architecture

The project maintains strict component separation:

```text
[ React Web Client ]  <--- (REST: /agents, /calls) --->  [ FastAPI Backend (:8000) ]
        |                                                              |
   (WebRTC Audio)                                               (Room & Dispatch)
        v                                                              v
[ LiveKit Server (:7880) ] <--- (WebRTC Media & Data Channels) ---> [ Python Agent Worker ]
```

1. **`api/` (FastAPI + SQLite)**:
   - Owns SQLite database access via `api/app/storage.py` (stdlib `sqlite3`, WAL mode, no ORM).
   - Manages agent profiles and call records.
   - Provisions LiveKit rooms with `empty_timeout=60` and explicit agent dispatches (`agent_name="support-agent"`).
   - Issues restricted customer access tokens (audio mic publish only, no whisper publish).
   - Exposes internal token-protected endpoints for worker notifications (`started`, `transcript`, `ended`).
2. **`agent/` (LiveKit Worker)**:
   - Subscribes to explicit room dispatches (`agent_name="support-agent"`).
   - Assembles pipeline: Deepgram STT (`nova-3`), Groq LLM (`qwen/qwen3.8-27b`), Deepgram TTS (`aura-asteria-en`), Silero VAD, and LiveKit Turn Detector.
   - Isolates customer audio via `RoomOptions(participant_identity=customer_identity, close_on_disconnect=True)`.
   - Discovers pre-connected participants to eliminate race conditions if customer joins before worker starts.
   - Captures customer's server-side join time (`participant.joined_at`) and logs worker detection latency.
   - Uses the framework's default process-per-job executor (`JobExecutorType.PROCESS`) to isolate crashes and memory between concurrent call sessions.
   - Tracks interruptions using a custom `TrackingLLM` wrapper, logging both full LLM-generated text and kept synchronized text (`[INTERRUPTION_DEBUG]`).
   - Synchronizes final call transcripts to the FastAPI backend via `POST /internal/calls/{id}/transcript` and triggers `POST /internal/calls/{id}/ended`.
3. **`web/` (React Client)**: Customer call interface and operator whisper view (Phase 5).
4. **`infra/` (LiveKit Server)**: Local Docker Compose deployment on macOS.

---

## API Endpoints & Curl Examples

### Public Endpoints

#### Create Agent (`POST /agents`)
```bash
curl -X POST http://localhost:8000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Custom Support Agent",
    "system_prompt": "You are a customer service assistant. Greet the customer and inform them this call is recorded.",
    "voice": "aura-asteria-en",
    "model": "llama-3.3-70b-versatile"
  }'
```

#### List Agents (`GET /agents`)
```bash
curl http://localhost:8000/agents
```

#### Start Call (`POST /calls`)
```bash
curl -X POST http://localhost:8000/calls \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "<agent_id>"}'
```
Returns:
```json
{
  "call_id": "5f84d691-...",
  "room_name": "call-822a488e",
  "token": "eyJhbGciOi...",
  "livekit_url": "ws://localhost:7880"
}
```

#### Get Call Status & Transcript (`GET /calls/{id}`)
```bash
curl http://localhost:8000/calls/<call_id>
```
*`transcript` is `null` while call is active or created. The transcript is exposed only after the call transitions to `ended`.*

---

## Ports and LiveKit config

### Port Reference Table

| Port / Protocol | Service / Function | Why It Exists |
| :--- | :--- | :--- |
| **`7880/tcp`** | HTTP REST API & WebSocket Signalling | Handles room management APIs, participant authentication, and WebRTC SDP offer/answer signalling over WebSocket. |
| **`7881/tcp`** | ICE-over-TCP Fallback | Transports WebRTC media over TCP when UDP traffic is blocked by strict client firewalls or network proxies. |
| **`7882/udp`** | WebRTC Media (UDP Mux) | Receives and routes all WebRTC RTP/RTCP audio and video packets through a single muxed UDP port (`rtc.udp_port`). |

### Local Development (macOS Docker Desktop) vs. Production (Linux)

| Deployment Aspect | Local Development (macOS Docker Desktop) | Production Deployment (Linux VM / Bare Metal) |
| :--- | :--- | :--- |
| **Networking Mode** | Docker Bridge Network with published host ports (`7880:7880`, `7881:7881`, `7882:7882/udp`). Host networking is not natively supported on macOS Docker Desktop. | Host Networking (`network_mode: host`) or bare-metal host deployment to eliminate Docker bridge NAT overhead and maximize throughput. |
| **UDP Media Ports** | Single-port UDP muxing on port **`7882/udp`** (`rtc.udp_port: 7882`). Publishing a large port range through macOS Docker Desktop degrades startup performance and consumes excessive host file descriptors. | Dynamic UDP port range **`50000-60000/udp`** (`rtc.port_range_start: 50000`, `rtc.port_range_end: 60000`). Firewall rules must allow inbound UDP traffic across this entire range. |
| **Candidate Advertising** | `rtc.use_external_ip: false` and `rtc.node_ip: 127.0.0.1`. LiveKit explicitly advertises `127.0.0.1` as the host candidate, ensuring macOS host browsers route media via Docker's port forwards rather than unreachable container bridge IPs (`172.x.x.x`). | `rtc.use_external_ip: true` to discover the node's public IP via STUN, or explicit `rtc.node_ip: <PUBLIC_IP>` when deployed behind static cloud NAT (e.g. AWS Elastic IP). |
| **Signalling & TLS** | Plain HTTP / WebSocket (`http://localhost:7880` and `ws://localhost:7880`) without encryption for local testing. | Reverse proxy (e.g., Caddy, Nginx, or cloud load balancer) terminating TLS on port **`443`** (`https://` and `wss://`), proxying back to `7880`. |
| **Embedded TURN Server** | **Disabled locally**. Not required because browser and server communicate directly via local host candidate (`127.0.0.1`). | **Enabled in `livekit.yaml`** (`turn:` block) for enterprise firewall traversal: <br>• `turn.udp_port: 3478` (TURN/UDP and STUN lookups; requires firewall UDP 3478 open)<br>• `turn.tls_port: 443` (TURN/TLS fallback mimicking HTTPS for networks blocking UDP; requires firewall TCP 443 open)<br>• `turn.cert_file` and `turn.key_file` configured with valid SSL certificates. |

### ICE Reachability Note
- The local server advertises `127.0.0.1` on port `7882` (UDP) and `7881` (TCP).
- A browser running on the macOS host connects directly to `localhost:7880` for signalling and sends media packets to `127.0.0.1:7882/udp`. Docker Desktop routes these packets to the container.

---

## Latency

*(Measured latency metrics will be recorded in Phase 4 per the latency-measurement skill).*

---

## Known limitations

- **No Authentication on Public Endpoints**: In this proof of concept, `/agents` and `/calls` endpoints are unauthenticated. Production requires OAuth/JWT session authentication and tenant separation.
- **Abandoned Calls Remain in "created"**: If a customer requests a call via `POST /calls` but never opens the WebRTC connection, the database call record remains in `created` status indefinitely (even though LiveKit closes the room after `empty_timeout=60`). A background sweeper or webhook is required in production.
- **Barge-In Truncation Mechanics (Exact Audio vs. Estimated Words)**: When customer interrupts agent speech, LiveKit accurately stops playout at the exact millisecond sample position (`playback_position`). However, because Deepgram WebSocket TTS sends raw binary PCM without word boundary events, LiveKit estimates word-level transcript truncation using its internal `SpeakingRateDetector` and syllable pacing (`livekit.agents.voice.transcription.synchronizer`). Kept text is an estimated truncation rather than exact word timestamps.
- **Process-Per-Job Execution & Host Memory**: The worker uses LiveKit's default process-per-job executor (`JobExecutorType.PROCESS`). Each call runs in an isolated child process, guaranteeing that an unhandled crash or memory leak in one session cannot affect others. In production mode (`start`), pre-warmed idle processes (`num_idle_processes=10` default) are maintained, requiring sufficient host memory.
- **Blocking SQLite in Async Handlers**: SQLite queries execute synchronously in FastAPI async route handlers. This is an accepted tradeoff for POC simplicity.
- **Host-Bound Local Agent**: Setting `rtc.node_ip: 127.0.0.1` functions seamlessly when the agent worker and client browser run directly on the host machine. If the agent worker were containerized inside Docker, an internal service DNS name or bridge networking strategy would be required.
- **Single UDP Port Muxing**: Muxing all media over `7882/udp` is optimal for development on macOS, but production high-scale deployments require the full `50000-60000/udp` range.
- **No Local TLS/TURN**: The local environment uses unencrypted WebSocket (`ws://`) and lacks TURN relay servers.
- **WebRTC Diagnostics via Browser Internals**: Because `livekit-client` keeps underlying `RTCPeerConnection` instances (`RTCEngine` and `PCTransportManager`) private without exposing public stats getters, active ICE candidate pair types (`host` vs `srflx` vs `relay`) must be inspected directly via `chrome://webrtc-internals` rather than via private property access in application code.
