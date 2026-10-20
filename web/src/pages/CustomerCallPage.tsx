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
import { Agent, CallState, AgentState, LiveTranscriptItem, CallResponse } from '../types';
import { fetchAgents, createCall, getCall } from '../services/api';

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

  // Live transcript items
  const [liveTranscript, setLiveTranscript] = useState<LiveTranscriptItem[]>([]);

  // Persisted call response fetched from API after call finishes
  const [savedCall, setSavedCall] = useState<CallResponse | null>(null);
  const [isPollingSavedCall, setIsPollingSavedCall] = useState<boolean>(false);

  // Refs for audio playback and LiveKit Room lifecycle
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const roomRef = useRef<Room | null>(null);
  const transcriptBoxRef = useRef<HTMLDivElement | null>(null);
  const agentTimeoutTimerRef = useRef<number | null>(null);

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
      if (roomRef.current) {
        console.log('[CALL] Unmounting CustomerCallPage, disconnecting room');
        roomRef.current.disconnect();
        roomRef.current = null;
      }
    };
  }, []);

  // Auto-scroll live transcript box when new messages arrive
  useEffect(() => {
    if (transcriptBoxRef.current) {
      transcriptBoxRef.current.scrollTop = transcriptBoxRef.current.scrollHeight;
    }
  }, [liveTranscript]);

  // Selected agent object
  const currentAgent = agents.find((a) => a.id === selectedAgentId);

  /**
   * Start a call. Initiated strictly by user click gesture (required for audio autoplay).
   */
  const handleStartCall = async () => {
    if (!selectedAgentId) return;

    // Reset previous call states
    setErrorMessage(null);
    setAgentWaitTimeout(false);
    setLiveTranscript([]);
    setSavedCall(null);
    setAgentState(null);
    setCallState('connecting');

    try {
      console.log(`[CALL] Initiating POST /calls for agent_id=${selectedAgentId}`);
      const callData = await createCall(selectedAgentId);
      setActiveCallId(callData.call_id);
      console.log(`[CALL] Created call_id=${callData.call_id}, room=${callData.room_name}`);

      // Create a fresh Room instance for this call
      const room = new Room({
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      roomRef.current = room;

      // Log public WebRTC connection state changes
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

      // Register text stream handler for 'lk.transcription' ONCE on this room
      room.registerTextStreamHandler(
        'lk.transcription',
        async (reader, participantInfo: { identity: string }) => {
          const segmentId =
            reader.info.attributes?.['lk.segment_id'] || reader.info.id || Math.random().toString();
          const sender = participantInfo.identity;
          const isUser =
            sender === room.localParticipant.identity || sender.startsWith('customer-');
          const speaker: 'You' | 'Agent' = isUser ? 'You' : 'Agent';
          const isFinalAttribute = reader.info.attributes?.['lk.transcription_final'] === 'true';

          let accumulatedText = '';

          // Stream chunks incrementally as yielded by TextStreamReader
          for await (const chunk of reader) {
            const timestamp = performance.now();
            accumulatedText += chunk;

            // Log each individual chunk for verification during barge-in review
            console.log(
              `[TRANSCRIPTION CHUNK] t=${timestamp.toFixed(2)}ms | sender=${sender} | segment=${segmentId} | final=${isFinalAttribute} | chunk="${chunk}"`
            );

            // Update or insert segment in live transcript state
            setLiveTranscript((prev) => {
              const existingIndex = prev.findIndex((item) => item.id === segmentId);
              if (existingIndex >= 0) {
                const updated = [...prev];
                updated[existingIndex] = {
                  ...updated[existingIndex],
                  text: accumulatedText,
                };
                return updated;
              } else {
                return [
                  ...prev,
                  {
                    id: segmentId,
                    speaker,
                    text: accumulatedText,
                    isFinal: isFinalAttribute,
                    timestamp,
                  },
                ];
              }
            });
          }

          // Stream ended
          const endTimestamp = performance.now();
          console.log(
            `[TRANSCRIPTION STREAM END] t=${endTimestamp.toFixed(2)}ms | sender=${sender} | segment=${segmentId} | final=${isFinalAttribute} | total="${accumulatedText}"`
          );

          // Mark segment as final when stream completes
          setLiveTranscript((prev) => {
            const existingIndex = prev.findIndex((item) => item.id === segmentId);
            if (existingIndex >= 0) {
              const updated = [...prev];
              updated[existingIndex] = {
                ...updated[existingIndex],
                text: accumulatedText,
                isFinal: true,
              };
              return updated;
            }
            return prev;
          });
        }
      );

      // Connect to LiveKit server with token
      console.log(`[WEBRTC] Connecting to ${callData.livekit_url}...`);
      await room.connect(callData.livekit_url, callData.token);
      console.log('[WEBRTC] Room connected successfully');

      // Enable microphone with echo cancellation
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
      const message = err instanceof Error ? err.message : String(err);
      console.error('[CALL] Call initiation failed:', message);
      setErrorMessage(`Call failed: ${message}`);
      setCallState('error');
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
    if (agentTimeoutTimerRef.current) {
      window.clearTimeout(agentTimeoutTimerRef.current);
      agentTimeoutTimerRef.current = null;
    }
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

  /**
   * Common handler when call session finishes: polls backend until call status is 'ended'.
   */
  const handleCallFinished = (callId: string) => {
    setCallState('ended');
    setAgentState(null);
    setAgentWaitTimeout(false);
    pollSavedCallTranscript(callId);
  };

  /**
   * Poll GET /calls/{id} up to 10 attempts (1 per second) to retrieve persisted transcript.
   */
  const pollSavedCallTranscript = async (callId: string) => {
    setIsPollingSavedCall(true);
    let attempts = 0;
    const maxAttempts = 10;

    const intervalId = window.setInterval(async () => {
      attempts += 1;
      try {
        const call = await getCall(callId);
        if (call.status === 'ended' || attempts >= maxAttempts) {
          window.clearInterval(intervalId);
          setIsPollingSavedCall(false);
          setSavedCall(call);
          console.log('[API] Retrieved finalized call record:', call);
        }
      } catch (pollErr) {
        console.warn(`[API] Poll attempt ${attempts} failed:`, pollErr);
        if (attempts >= maxAttempts) {
          window.clearInterval(intervalId);
          setIsPollingSavedCall(false);
        }
      }
    }, 1000);
  };

  return (
    <div className="customer-page">
      {/* Hidden audio element for LiveKit agent audio playout */}
      <audio ref={audioRef} autoPlay />

      {/* Error alert banner */}
      {errorMessage && <div className="error-banner">{errorMessage}</div>}

      {/* Agent timeout alert banner */}
      {agentWaitTimeout && (
        <div className="warning-banner">
          Waiting for support agent to join room... (No agent worker joined after 20s. Ensure{' '}
          <code>uv run python agent.py dev</code> is running).
        </div>
      )}

      {/* Agent Picker & Info Card */}
      <div className="card">
        <div className="agent-picker">
          <label htmlFor="agent-select">Select Support Agent:</label>
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
            <div>
              <strong>Model:</strong> {currentAgent.model}
            </div>
            <div>
              <strong>Voice:</strong> {currentAgent.voice}
            </div>
            <div>
              <strong>Prompt:</strong> {currentAgent.system_prompt}
            </div>
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

          <div className="status-indicator">
            <span>Status: {callState}</span>
            {agentState && <span className="state-tag">[Agent: {agentState}]</span>}
          </div>
        </div>
      </div>

      {/* Live Transcript Card */}
      <div className="card transcript-section">
        <div className="transcript-header">
          <span>Live Conversation Transcript</span>
          {callState === 'in_call' && <span className="state-tag">Active Stream</span>}
        </div>

        <div className="transcript-box" ref={transcriptBoxRef}>
          {liveTranscript.length === 0 ? (
            <div className="empty-transcript">
              {callState === 'in_call'
                ? 'Connected. Start speaking into your microphone...'
                : 'No active call. Select an agent and click "Call Agent" to begin.'}
            </div>
          ) : (
            liveTranscript.map((item) => (
              <div
                key={item.id}
                className={`transcript-line ${item.speaker === 'You' ? 'you' : 'agent'}`}
              >
                <span
                  className={`speaker-label ${item.speaker === 'You' ? 'you-label' : 'agent-label'}`}
                >
                  [{item.speaker}]
                </span>
                <span>{item.text}</span>
                {!item.isFinal && <span className="interim-indicator">...</span>}
              </div>
            ))
          )}
        </div>
      </div>

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
