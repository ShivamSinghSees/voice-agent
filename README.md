# Operator-Guided Voice AI Agent

*Work in progress*

Proof-of-concept operator-guided voice AI agent built with a self-hosted LiveKit server, Python LiveKit Agents worker, FastAPI backend, and React web client with customer and operator views.

---

## Setup

### Prerequisites
- macOS (Apple Silicon or Intel) or Linux
- Docker Desktop or Docker Engine with Docker Compose v2+
- Python 3.11+
- Node.js 20+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package and environment manager)

### 1. Environment Configuration
Copy `.env.example` to `.env` and populate your secrets:
```bash
cp .env.example .env
```
Ensure `.env` contains:
```dotenv
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=your_generated_api_key
LIVEKIT_API_SECRET=your_generated_api_secret
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

---

## Architecture

*(To be expanded in Phase 2)*

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
- If testing from an external device on the local network (e.g. a mobile phone), `rtc.node_ip` in `infra/livekit.yaml` must be temporarily updated to the Mac host's LAN IP (e.g. `192.168.1.x`), and `.env` `LIVEKIT_URL` must point to that IP.

---

## Latency

*(Measured latency metrics will be recorded in Phase 4 per the latency-measurement skill).*

---

## Known limitations

- **Host-Bound Local Agent**: Setting `rtc.node_ip: 127.0.0.1` functions seamlessly when the agent worker and client browser run directly on the host machine. If the agent worker were containerized inside Docker, an internal service DNS name or bridge networking strategy would be required.
- **Single UDP Port Muxing**: Muxing all media over `7882/udp` is optimal for development on macOS, but production high-scale deployments require the full `50000-60000/udp` range.
- **No Local TLS/TURN**: The local environment uses unencrypted WebSocket (`ws://`) and lacks TURN relay servers.
