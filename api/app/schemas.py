"""Pydantic schemas for request validation and response serialization."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


# --- Agent Schemas ---

class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: str = Field(..., min_length=1)
    voice: str = Field(..., min_length=1, max_length=100)
    model: str = Field(..., min_length=1, max_length=100)


class AgentResponse(BaseModel):
    id: str
    name: str
    system_prompt: str
    voice: str
    model: str
    created_at: str


# --- Call Schemas ---

class CallCreate(BaseModel):
    agent_id: str = Field(..., min_length=1)


class CallStartResponse(BaseModel):
    call_id: str
    room_name: str
    token: str
    livekit_url: str


class TranscriptLineResponse(BaseModel):
    seq: int
    speaker: Literal["agent", "user"]
    text: str
    started_at: str
    ended_at: str


class RecordingNoticeResult(BaseModel):
    passed: bool
    evidence_seq: Optional[int] = None
    offset_seconds: Optional[float] = None
    reason: str


class RefundPromiseResult(BaseModel):
    status: Literal["done", "error"]
    passed: Optional[bool] = None
    offending_seq: Optional[int] = None
    offending_text: Optional[str] = None
    reason: str


class CallComplianceResponse(BaseModel):
    status: Literal["pending", "done", "error"]
    recording_notice: Optional[RecordingNoticeResult] = None
    refund_promise: Optional[RefundPromiseResult] = None
    checked_at: Optional[str] = None


class CallResponse(BaseModel):
    id: str
    agent_id: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    transcript: Optional[list[TranscriptLineResponse]] = None
    compliance: Optional[CallComplianceResponse] = None


class CallSummaryResponse(BaseModel):
    id: str
    agent_id: str
    agent_name: Optional[str] = None
    room_name: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


class OperatorTokenResponse(BaseModel):
    call_id: str
    room_name: str
    token: str
    livekit_url: str


# --- Internal Schemas (Agent Worker -> API) ---

class InternalCallStarted(BaseModel):
    started_at: str


class InternalTranscriptLine(BaseModel):
    seq: int = Field(..., ge=0)
    speaker: Literal["agent", "user"]
    text: str
    started_at: str
    ended_at: str


class InternalTranscriptLines(BaseModel):
    lines: list[InternalTranscriptLine]


class InternalCallEnded(BaseModel):
    ended_at: str
