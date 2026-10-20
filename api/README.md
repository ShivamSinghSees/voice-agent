# API Service

FastAPI backend application for the operator-guided voice AI agent POC.

## Purpose & Scope
- Exposes REST endpoints for call lifecycle management, token issuance, and post-call compliance verification.
- Owns persistent data storage via a dedicated `storage` module using stdlib `sqlite3` (no ORM, WAL mode, parameterized queries).
- Does not contain LiveKit worker process logic or React frontend code.
- Provisions LiveKit rooms with `empty_timeout=60`, registers explicit agent dispatches (`agent_name="support-agent"`), and mints restricted customer tokens.

---

## Setup & Running

### 1. Run Migrations & Start Server
Run from the `api/` directory (or use `uv --directory api`):
```bash
# Start FastAPI development server
uv run uvicorn app.main:app --port 8000
```
On startup, the service automatically initializes SQLite tables and seeds a default demo agent ("Acme Telecom Support") if no agents exist.

### 2. Run Automated Unit Tests
```bash
uv run pytest
```

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
    "model": "qwen/qwen3.8-27b"
  }'
```
> **Note on Voice / Model IDs**: `aura-asteria-en` (Deepgram) and `qwen/qwen3.8-27b` (Groq, ~182ms TTFT) are verified and active in the agent worker pipeline.

#### List Agents (`GET /agents`)
```bash
curl http://localhost:8000/agents
```

#### Get Agent (`GET /agents/{id}`)
```bash
curl http://localhost:8000/agents/<agent_id>
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
*Note: `transcript` is `null` while call is `created` or `active`. The transcript is revealed only after status transitions to `ended`.*

#### List Active Calls (`GET /calls?status=active`)
```bash
curl "http://localhost:8000/calls?status=active"
```

---

### Internal Endpoints (Worker -> API)
Protected by header `X-Internal-Token: <INTERNAL_API_TOKEN>`. Returns 401 Unauthorized if missing or incorrect.

- `POST /internal/calls/{id}/started` `{ "started_at": "2026-10-20T10:00:00Z" }`
- `POST /internal/calls/{id}/transcript` `{ "lines": [ { "seq": 0, "speaker": "agent", "text": "...", "started_at": "...", "ended_at": "..." } ] }`
- `POST /internal/calls/{id}/ended` `{ "ended_at": "2026-10-20T10:05:00Z" }`
