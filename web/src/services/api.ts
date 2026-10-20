import { Agent, CallStartResponse, CallResponse } from '../types';

// Read API base URL from Vite environment variable or default to localhost:8000
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

/**
 * Fetch all available agents from the backend for the customer picker.
 */
export async function fetchAgents(): Promise<Agent[]> {
  const resp = await fetch(`${API_BASE_URL}/agents`, {
    headers: {
      Accept: 'application/json',
    },
  });
  if (!resp.ok) {
    throw new Error(`Failed to fetch agents: HTTP ${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

/**
 * Initiate a call session with the chosen agent.
 * Backend creates LiveKit room and dispatches support worker.
 */
export async function createCall(agentId: string): Promise<CallStartResponse> {
  const resp = await fetch(`${API_BASE_URL}/calls`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({ agent_id: agentId }),
  });
  if (!resp.ok) {
    const errorBody = await resp.text().catch(() => '');
    throw new Error(`Failed to create call (HTTP ${resp.status}): ${errorBody || resp.statusText}`);
  }
  return resp.json();
}

/**
 * Retrieve current call details including status and persisted transcript.
 */
export async function getCall(callId: string): Promise<CallResponse> {
  const resp = await fetch(`${API_BASE_URL}/calls/${callId}`, {
    headers: {
      Accept: 'application/json',
    },
  });
  if (!resp.ok) {
    throw new Error(`Failed to fetch call details: HTTP ${resp.status}`);
  }
  return resp.json();
}
