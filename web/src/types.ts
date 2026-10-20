// Data model for an AI Agent returned by GET /agents
export interface Agent {
  id: string;
  name: string;
  system_prompt: string;
  voice: string;
  model: string;
  created_at: string;
}

// Response from backend when initiating a call via POST /calls
export interface CallStartResponse {
  call_id: string;
  room_name: string;
  token: string;
  livekit_url: string;
}

// Single transcript line as saved in the backend database
export interface TranscriptLine {
  seq: number;
  speaker: 'agent' | 'user';
  text: string;
  started_at: string;
  ended_at: string;
}

// Complete call record returned by GET /calls/{id}
export interface CallResponse {
  id: string;
  agent_id: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  transcript?: TranscriptLine[] | null;
  compliance?: Record<string, string> | null;
}

// Real-time transcript segment displayed on the customer call screen
export interface LiveTranscriptItem {
  id: string; // segment_id from lk.segment_id or stream ID
  speaker: 'You' | 'Agent';
  text: string;
  isFinal: boolean;
  timestamp: number; // performance.now() timestamp when first created
}

// Overall customer page call lifecycle state
export type CallState = 'idle' | 'connecting' | 'in_call' | 'ended' | 'error';

// Real-time voice agent state published in participant attribute 'lk.agent.state'
export type AgentState = 'initializing' | 'idle' | 'listening' | 'thinking' | 'speaking';
