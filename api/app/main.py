"""FastAPI main application module.

Implements public endpoints for agent management, call initialization, and call status,
along with internal token-protected endpoints for worker lifecycle notifications.
"""

import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from app import compliance, config, schemas, storage
from app.livekit_service import LiveKitServiceError, create_operator_token, create_room_and_dispatch
from seed import seed_demo_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite tables and seed demo agent on application startup
    storage.init_db(config.DATABASE_PATH)
    seed_demo_agent(config.DATABASE_PATH)
    yield


app = FastAPI(
    title="Operator-Guided Voice AI Agent API",
    description="Backend API for managing agents, call lifecycle, and LiveKit session coordination.",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure Cross-Origin Resource Sharing (CORS) for web client (e.g. Vite dev server on :5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Security Dependency for Internal Endpoints ---

def verify_internal_token(
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> str:
    """Validate that the caller presents the correct internal pre-shared key.

    Uses secrets.compare_digest to prevent timing-attack side-channel leakage.
    """
    if not x_internal_token or not config.INTERNAL_API_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Missing internal API authentication token",
        )
    if not secrets.compare_digest(x_internal_token, config.INTERNAL_API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid internal API authentication token",
        )
    return x_internal_token


# --- Public Endpoints ---

# Note on concurrency architecture:
# Route handlers calling livekit-api are async to prevent blocking on network I/O.
# Synchronous sqlite3 calls executed directly inside async handlers are an accepted POC tradeoff,
# avoiding external ORM/threadpool overhead while handling POC concurrency requirements.

@app.post(
    "/agents",
    response_model=schemas.AgentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Agents"],
)
async def create_agent(payload: schemas.AgentCreate):
    """Create a new voice agent profile."""
    agent_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    agent = storage.create_agent(
        db_path=config.DATABASE_PATH,
        agent_id=agent_id,
        name=payload.name,
        system_prompt=payload.system_prompt,
        voice=payload.voice,
        model=payload.model,
        created_at=now_iso,
    )
    return agent


@app.get(
    "/agents",
    response_model=list[schemas.AgentResponse],
    tags=["Agents"],
)
async def list_agents():
    """List all available agents for the web client picker."""
    return storage.list_agents(config.DATABASE_PATH)


@app.get(
    "/agents/{agent_id}",
    response_model=schemas.AgentResponse,
    tags=["Agents"],
)
async def get_agent(agent_id: str):
    """Retrieve an agent profile by ID."""
    agent = storage.get_agent(config.DATABASE_PATH, agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    return agent


@app.post(
    "/calls",
    response_model=schemas.CallStartResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Calls"],
)
async def create_call(payload: schemas.CallCreate):
    """Initialize a call session.

    Validates agent existence, registers call in DB, provisions LiveKit room with
    empty_timeout, creates explicit agent dispatch, and mints customer AccessToken.
    Marks call as 'failed' and returns 502 Bad Gateway if LiveKit is unreachable.
    """
    agent = storage.get_agent(config.DATABASE_PATH, payload.agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{payload.agent_id}' does not exist",
        )

    call_id = str(uuid.uuid4())
    room_name = f"call-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Create call record in DB with initial status 'created'
    call = storage.create_call(
        db_path=config.DATABASE_PATH,
        call_id=call_id,
        agent_id=payload.agent_id,
        room_name=room_name,
        created_at=now_iso,
    )

    try:
        # Provision room, explicit dispatch, and customer token on LiveKit
        customer_token = await create_room_and_dispatch(
            room_name=room_name,
            call_id=call_id,
            agent_id=payload.agent_id,
        )
        return schemas.CallStartResponse(
            call_id=call_id,
            room_name=room_name,
            token=customer_token,
            livekit_url=config.LIVEKIT_URL,
        )
    except LiveKitServiceError as exc:
        # Mark call failed in database and return clean 502 to client
        storage.mark_call_failed(config.DATABASE_PATH, call_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to provision LiveKit call session: {exc}",
        ) from exc


@app.get(
    "/calls/{call_id}",
    response_model=schemas.CallResponse,
    tags=["Calls"],
)
async def get_call(call_id: str):
    """Retrieve call details.

    Transcript lines are hidden (null) while call is active or created, and exposed
    ONLY after the call status has reached 'ended'.
    """
    call = storage.get_call(config.DATABASE_PATH, call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found",
        )

    # Transcript gate: Only expose transcript after call has completed
    transcript_data = None
    if call["status"] == "ended":
        raw_lines = storage.get_transcript_lines(config.DATABASE_PATH, call_id)
        transcript_data = [schemas.TranscriptLineResponse(**line) for line in raw_lines]

    # Compliance gate: Only expose compliance after call has reached 'ended'
    compliance_data = None
    if call["status"] == "ended" and call.get("compliance"):
        try:
            raw_compliance = json.loads(call["compliance"])
            compliance_data = schemas.CallComplianceResponse(**raw_compliance)
        except Exception:
            compliance_data = None

    return schemas.CallResponse(
        id=call["id"],
        agent_id=call["agent_id"],
        status=call["status"],
        created_at=call["created_at"],
        started_at=call["started_at"],
        ended_at=call["ended_at"],
        transcript=transcript_data,
        compliance=compliance_data,
    )


@app.get(
    "/calls",
    response_model=list[schemas.CallSummaryResponse],
    tags=["Calls"],
)
async def list_calls(status_filter: Optional[str] = Query(None, alias="status")):
    """List calls, optionally filtered by status (e.g. GET /calls?status=active for operator view)."""
    calls = storage.list_calls(config.DATABASE_PATH, status=status_filter)
    return [schemas.CallSummaryResponse(**c) for c in calls]


@app.post(
    "/calls/{call_id}/operator-token",
    response_model=schemas.OperatorTokenResponse,
    tags=["Calls"],
)
async def get_operator_token(call_id: str):
    """Generate a hidden, listen-only token for a supervisor/operator to join a call.

    Validates:
    - 404 if call does not exist
    - 409 Conflict if call has already ended or failed
    """
    call = storage.get_call(config.DATABASE_PATH, call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found",
        )

    if call["status"] in ("ended", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot monitor call '{call_id}' with status '{call['status']}'",
        )

    token = create_operator_token(room_name=call["room_name"], call_id=call_id)
    return schemas.OperatorTokenResponse(
        call_id=call_id,
        room_name=call["room_name"],
        token=token,
        livekit_url=config.LIVEKIT_URL,
    )


# --- Internal Endpoints (Worker -> API) ---

@app.post(
    "/internal/calls/{call_id}/started",
    tags=["Internal"],
)
async def mark_call_started(
    call_id: str,
    payload: schemas.InternalCallStarted,
    _: str = Depends(verify_internal_token),
):
    """Idempotent notification from agent worker when audio connection starts."""
    call = storage.get_call(config.DATABASE_PATH, call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found",
        )
    updated = storage.set_call_started(config.DATABASE_PATH, call_id, payload.started_at)
    return {"status": "ok", "call": updated}


@app.post(
    "/internal/calls/{call_id}/transcript",
    tags=["Internal"],
)
async def upsert_transcript(
    call_id: str,
    payload: schemas.InternalTranscriptLines,
    _: str = Depends(verify_internal_token),
):
    """Idempotently batch upsert transcript lines generated during the conversation."""
    call = storage.get_call(config.DATABASE_PATH, call_id)
    if not call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found",
        )
    lines_dicts = [line.model_dump() for line in payload.lines]
    storage.upsert_transcript_lines(config.DATABASE_PATH, call_id, lines_dicts)
    return {"status": "ok", "count": len(lines_dicts)}


@app.post(
    "/internal/calls/{call_id}/ended",
    tags=["Internal"],
)
async def mark_call_ended(
    call_id: str,
    payload: schemas.InternalCallEnded,
    background_tasks: BackgroundTasks,
    _: str = Depends(verify_internal_token),
):
    """Idempotent notification from worker when call concludes.

    Atomically transitions status to 'ended' using UPDATE ... WHERE status != 'ended'.
    Schedules the compliance evaluation background task only if this call transitioned the record (rowcount == 1).
    """
    initial_compliance = json.dumps({
        "status": "pending",
        "recording_notice": None,
        "refund_promise": None,
        "checked_at": None,
    })

    updated_call, transitioned = storage.transition_call_to_ended(
        config.DATABASE_PATH,
        call_id,
        payload.ended_at,
        initial_compliance_json=initial_compliance,
    )
    if not updated_call:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Call '{call_id}' not found",
        )

    if transitioned:
        background_tasks.add_task(compliance.run_post_call_compliance, call_id)

    return {"status": "ended", "call": updated_call}
