import React, { useState, useEffect, useRef } from 'react';
import {
  Room,
  RoomEvent,
  RemoteTrack,
  RemoteTrackPublication,
  RemoteParticipant,
  Participant,
  Track,
  ConnectionState,
} from 'livekit-client';
import { Agent, CallState, AgentState, CallResponse } from '../types';
import { fetchAgents, createCall, getCall } from '../services/api';
import { useLiveTranscript } from '../hooks/useLiveTranscript';
import { TranscriptPanel } from '../components/TranscriptPanel';
import { useCallGuard } from '../context/CallGuardContext';

const STATUS_LABELS: Record<CallState, string> = {
  idle: 'Ready to call',
  connecting: 'Connecting...',
  in_call: 'In call',
  ended: 'Call ended',
  error: 'Something went wrong',
};

export const CustomerCallPage: React.FC = () => {
  // Available agents list loaded from GET /agents
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>('');

  // Call lifecycle state
  const [callState, setCallState] = useState<CallState>('idle');
  const [activeCallId, setActiveCallId] = useState<string | null>(null);
  const [agentState, setAgentState] = useState<AgentState | null>(null);

  // Error and warning alerts
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [agentWaitTimeout, setAgentWaitTimeout] = useState<boolean>(false);

  // Live transcript managed via dedicated hook
  const { lines: liveTranscript, attachRoom, detachRoom, clearTranscript } = useLiveTranscript();

  // Persisted call response fetched from API after call finishes
  const [savedCall, setSavedCall] = useState<CallResponse | null>(null);
  const [isPollingSavedCall, setIsPollingSavedCall] = useState<boolean>(false);

  // Refs for audio playback and LiveKit Room lifecycle
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const roomRef = useRef<Room | null>(null);
  const agentTimeoutTimerRef = useRef<number | null>(null);

  // Navigation-guard support
  const { setCallActive, registerEndCall } = useCallGuard();
  // Set in the effect body (not just initialised) so it is correct after StrictMode's remount.
  const isMountedRef = useRef<boolean>(false);
  // Bumped by every start/hang-up; lets an in-flight start notice it has been superseded.
  const callAttemptRef = useRef<number>(0);
  // Ensures handleCallFinished (and its polling) runs once per call.
  const finishedCallIdRef = useRef<string | null>(null);
  // Always points at the latest handleHangUp so the registered callback can stay stable.
  const hangUpRef = useRef<() => void>(() => {});

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Load available agents on initial page mount
  useEffect(() => {
    let isMounted = true;
    fetchAgents()
      .then((data) => {
        if (!isMounted) return;
        setAgents(data);
        if (data.length > 0) {
          setSelectedAgentId(data[0].id);
        }
      })
      .catch((err: Error) => {
        if (!isMounted) return;
        setErrorMessage(`Failed to load agents: ${err.message}. Ensure backend is running.`);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  // Cleanup room connection if component unmounts
  useEffect(() => {
    return () => {
      if (agentTimeoutTimerRef.current) {
        window.clearTimeout(agentTimeoutTimerRef.current);
      }
      detachRoom();
      if (roomRef.current) {
        console.log('[CALL] Unmounting CustomerCallPage, disconnecting room');
        roomRef.current.disconnect();
        roomRef.current = null;
      }
    };
  }, [detachRoom]);

  // Selected agent object
  const currentAgent = agents.find((a) => a.id === selectedAgentId);

  /**
   * Start a call. Initiated strictly by user click gesture (required for audio autoplay).
   */
  const handleStartCall = async () => {
    if (!selectedAgentId) return;

    // Reset previous call states
    const attempt = ++callAttemptRef.current;
    finishedCallIdRef.current = null;
    setErrorMessage(null);
    setAgentWaitTimeout(false);
    clearTranscript();
    setSavedCall(null);
    setActiveCallId(null);
    setAgentState(null);
    setCallState('connecting');

    try {
      console.log(`[CALL] Initiating POST /calls for agent_id=${selectedAgentId}`);
      const callData = await createCall(selectedAgentId);
      // Hung up or navigated away while the call was being created: do not open a room.
      if (attempt !== callAttemptRef.current || !isMountedRef.current) return;
      setActiveCallId(callData.call_id);
      console.log(`[CALL] Created call_id=${callData.call_id}, room=${callData.room_name}`);

      // 1. Create a fresh Room instance for this call
      const room = new Room({
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      roomRef.current = room;

      // 2. Synchronously attach room to register 'lk.transcription' handler BEFORE room.connect()
      attachRoom(room);

      // 3. Log public WebRTC connection state changes
      room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        console.log(`[WEBRTC] ConnectionStateChanged: ${state}`);
        if (state === ConnectionState.Connected) {
          setCallState('in_call');
        } else if (state === ConnectionState.Disconnected) {
          console.log('[WEBRTC] Room disconnected');
        }
      });

      // Listen for incoming audio tracks from the agent
      room.on(
        RoomEvent.TrackSubscribed,
        (track: RemoteTrack, _pub: RemoteTrackPublication, participant: RemoteParticipant) => {
          console.log(
            `[WEBRTC] TrackSubscribed: kind=${track.kind}, sid=${track.sid}, participant=${participant.identity}`
          );
          if (track.kind === Track.Kind.Audio && audioRef.current) {
            // Attach remote audio track to HTML audio element for browser playout
            track.attach(audioRef.current);
            console.log('[WEBRTC] Attached agent audio track to HTMLAudioElement');
          }
        }
      );

      // Clean up track when unsubscribed
      room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
        track.detach();
      });

      // Track agent state changes via participant attributes ('lk.agent.state')
      room.on(
        RoomEvent.ParticipantAttributesChanged,
        (changedAttributes: Record<string, string>, participant: Participant) => {
          if (!participant.identity.startsWith('customer-')) {
            const state = changedAttributes['lk.agent.state'] as AgentState | undefined;
            if (state) {
              console.log(`[AGENT STATE] ${participant.identity} -> ${state}`);
              setAgentState(state);
            }
          }
        }
      );

      // Detect remote agent joining to clear 20-second timeout warning
      room.on(RoomEvent.ParticipantConnected, (participant: RemoteParticipant) => {
        console.log(`[ROOM] Participant connected: ${participant.identity}`);
        if (!participant.identity.startsWith('customer-')) {
          setAgentWaitTimeout(false);
          if (agentTimeoutTimerRef.current) {
            window.clearTimeout(agentTimeoutTimerRef.current);
            agentTimeoutTimerRef.current = null;
          }
        }
      });

      // Disconnect handling when server closes room or session terminates
      room.on(RoomEvent.Disconnected, () => {
        console.log('[ROOM] Disconnected event received');
        handleCallFinished(callData.call_id);
      });

      // 4. Connect to LiveKit server with token (handler already registered and active)
      console.log(`[WEBRTC] Connecting to ${callData.livekit_url}...`);
      await room.connect(callData.livekit_url, callData.token);
      if (attempt !== callAttemptRef.current || !isMountedRef.current) {
        if (room.state !== ConnectionState.Disconnected) room.disconnect();
        return;
      }
      console.log('[WEBRTC] Room connected successfully');

      // 5. Enable microphone with echo cancellation
      try {
        await room.localParticipant.setMicrophoneEnabled(true, {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        });
        console.log('[AUDIO] Microphone enabled with echoCancellation: true');
      } catch (micErr: unknown) {
        const message = micErr instanceof Error ? micErr.message : String(micErr);
        console.error('[AUDIO] Microphone permission denied or device error:', message);
        setErrorMessage(`Microphone access error: ${message}. Please check browser permissions.`);
      }

      // Check if remote agent is already connected, otherwise start 20s timeout
      const hasRemoteAgent = Array.from(room.remoteParticipants.values()).some(
        (p) => !p.identity.startsWith('customer-')
      );
      if (!hasRemoteAgent) {
        agentTimeoutTimerRef.current = window.setTimeout(() => {
          console.warn('[CALL] 20 seconds elapsed without remote agent participant');
          setAgentWaitTimeout(true);
        }, 20000);
      }
    } catch (err: unknown) {
      // A hang-up/unmount during connect rejects room.connect(); that is not a failure.
      if (attempt !== callAttemptRef.current || !isMountedRef.current) return;
      console.error('[CALL] Failed to start call:', err);
      const message = err instanceof Error ? err.message : String(err);
      setErrorMessage(`Call setup failed: ${message}`);
      setCallState('error');
      detachRoom();
      if (roomRef.current) {
        roomRef.current.disconnect();
        roomRef.current = null;
      }
    }
  };

  /**
   * User manually clicks Hang Up.
   */
  const handleHangUp = () => {
    console.log('[CALL] User clicked Hang Up');
    callAttemptRef.current += 1;
    if (agentTimeoutTimerRef.current) {
      window.clearTimeout(agentTimeoutTimerRef.current);
      agentTimeoutTimerRef.current = null;
    }
    detachRoom();
    if (roomRef.current) {
      roomRef.current.disconnect();
      roomRef.current = null;
    }
    if (activeCallId) {
      handleCallFinished(activeCallId);
    } else {
      setCallState('ended');
    }
  };

  // Keep the registered end-call callback pointing at the latest handleHangUp.
  useEffect(() => {
    hangUpRef.current = handleHangUp;
  });

  // Register a stable end-call callback with the nav guard; clear it on unmount.
  useEffect(() => {
    registerEndCall(() => hangUpRef.current());
    return () => {
      registerEndCall(null);
    };
  }, [registerEndCall]);

  // Publish "call is active" (connecting or in_call); reset to false on change/unmount.
  const isCallActive = callState === 'connecting' || callState === 'in_call';
  useEffect(() => {
    setCallActive(isCallActive);
    return () => {
      setCallActive(false);
    };
  }, [isCallActive, setCallActive]);

  /**
   * Common handler when call session finishes: polls backend until call status is 'ended'.
   * Runs once per call: both Hang Up and the room Disconnected event reach this function.
   */
  const handleCallFinished = (callId: string) => {
    if (!isMountedRef.current) return;
    if (finishedCallIdRef.current === callId) return;
    finishedCallIdRef.current = callId;
    setCallState('ended');
    setAgentState(null);
    setAgentWaitTimeout(false);
    pollSavedCallTranscript(callId);
  };

  /**
   * Poll GET /calls/{id} until status is 'ended' or timeout (up to 30 attempts, 500ms intervals).
   * Verifies background transcript flushes and compliance checks complete.
   */
  const pollSavedCallTranscript = async (callId: string) => {
    if (!isMountedRef.current) return;
    setIsPollingSavedCall(true);
    let attempts = 0;
    const maxAttempts = 30;

    const poll = async () => {
      try {
        if (!isMountedRef.current) return;
        attempts += 1;
        console.log(`[POLL] Fetching GET /calls/${callId} (attempt ${attempts}/${maxAttempts})`);
        const data = await getCall(callId);
        if (!isMountedRef.current) return;
        if (data.status === 'ended') {
          console.log('[POLL] Call status is "ended", saved transcript loaded:', data.transcript);
          setSavedCall(data);
          setIsPollingSavedCall(false);
          return;
        }

        if (attempts < maxAttempts) {
          setTimeout(poll, 500);
        } else {
          console.warn('[POLL] Reached max polling attempts without "ended" status');
          setSavedCall(data);
          setIsPollingSavedCall(false);
        }
      } catch (err: unknown) {
        if (!isMountedRef.current) return;
        console.error('[POLL] Error fetching call data:', err);
        setIsPollingSavedCall(false);
      }
    };

    poll();
  };

  return (
    <div className="customer-page">
      {/* Hidden audio element for remote agent audio playout */}
      <audio ref={audioRef} autoPlay playsInline style={{ display: 'none' }} />

      {/* Error and timeout banners */}
      {errorMessage && <div className="error-banner">{errorMessage}</div>}
      {agentWaitTimeout && (
        <div className="warning-banner">
          Agent worker did not join room within 20 seconds. Ensure the agent worker process is running
          (<code>cd agent && uv run python agent.py dev</code>).
        </div>
      )}

      {/* Agent Selector Card */}
      <div className="card agent-card">
        <h2 className="card-title">Start a support call</h2>
        <p className="card-subtitle">
          Choose an agent, then click Call Agent. Allow microphone access when your browser asks.
        </p>

        <div className="agent-picker">
          <label htmlFor="agent-select">Support agent</label>
          <select
            id="agent-select"
            value={selectedAgentId}
            disabled={callState === 'connecting' || callState === 'in_call'}
            onChange={(e) => setSelectedAgentId(e.target.value)}
          >
            {agents.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        </div>

        {currentAgent && (
          <div className="agent-details">
            <div className="chip-row">
              <span className="chip">
                <span className="chip-label">Model</span> {currentAgent.model}
              </span>
              <span className="chip">
                <span className="chip-label">Voice</span> {currentAgent.voice}
              </span>
            </div>
            <details className="prompt-details">
              <summary>View agent instructions</summary>
              <p>{currentAgent.system_prompt}</p>
            </details>
          </div>
        )}

        {/* Controls and status row */}
        <div className="controls-row">
          <div>
            {callState === 'in_call' ? (
              <button className="btn-hangup" onClick={handleHangUp}>
                Hang Up
              </button>
            ) : (
              <button
                className="btn-call"
                disabled={callState === 'connecting' || agents.length === 0}
                onClick={handleStartCall}
              >
                {callState === 'connecting' ? 'Connecting...' : 'Call Agent'}
              </button>
            )}
          </div>

          <div className="status-indicator" role="status">
            <span className={`status-badge status-${callState}`}>
              <span className="status-dot" aria-hidden="true" />
              {STATUS_LABELS[callState]}
            </span>
            {agentState && <span className="state-tag">Agent: {agentState}</span>}
          </div>
        </div>
      </div>

      {/* Live Transcript Panel Component */}
      <TranscriptPanel items={liveTranscript} callState={callState} />

      {/* Persisted Server Transcript Card (Displayed upon call completion) */}
      {savedCall && (
        <div className="card">
          <div className="transcript-header">
            <span>Persisted Database Transcript (GET /calls/{savedCall.id})</span>
            <span className="state-tag">status: {savedCall.status}</span>
          </div>
          <div className="saved-transcript-box">
            {savedCall.transcript && savedCall.transcript.length > 0 ? (
              savedCall.transcript.map((line) => (
                <div key={line.seq} className="saved-transcript-row">
                  [{line.speaker.toUpperCase()}] {line.text} ({line.started_at} - {line.ended_at})
                </div>
              ))
            ) : (
              <div>No transcript records saved in database.</div>
            )}
          </div>
        </div>
      )}

      {isPollingSavedCall && (
        <div className="card" style={{ textAlign: 'center', color: '#9ca3af' }}>
          Polling backend database for finalized transcript...
        </div>
      )}
    </div>
  );
};
