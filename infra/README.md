# Infrastructure & Deployment

Local deployment configuration for self-hosted LiveKit and supporting services.

## Purpose & Scope
- Maintains self-hosted `livekit.yaml` configuration (signalling, TCP fallback, UDP RTC media port ranges).
- Orchestrates local infrastructure via Docker Compose (`docker-compose.yml`).
- Contains connection and validation test scripts in `scripts/`.

## Running LiveKit Server
Run from the repository root:
```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d
```

## Running Connection Smoke Test
Run from the repository root:
```bash
uv run --with livekit-api --with python-dotenv python3 infra/scripts/test_livekit_connection.py
```
