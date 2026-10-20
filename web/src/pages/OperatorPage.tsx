import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Room,
  RoomEvent,
  ParticipantKind,
  RemoteParticipant,
  RemoteTrack,
  RemoteTrackPublication,
  Track,
} from 'livekit-client';
import { ActiveCallSummary } from '../types';
import { fetchActiveCalls, getOperatorToken } from '../services/api';

export const OperatorPage: React.FC = () => {
  // Available active calls fetched from GET /calls?status=active
  const [activeCalls, setActiveCalls] = useState<ActiveCallSummary[]>([]);
  const [isFetchingCalls, setIsFetchingCalls] = useState<boolean>(false);

  // Monitoring session state
  const [monitoredCallId, setMonitoredCallId] = useState<string | null>(null);
  const [connectionState, setConnectionState] = useState<'idle' | 'connecting' | 'connected' | 'disconnected'>('idle');
  const [agentPresent, setAgentPresent] = useState<boolean>(false);
  const [agentIdentity, setAgentIdentity] = useState<string | null>(null);
  const [customerPresent, setCustomerPresent] = useState<boolean>(false);

  // Whisper input and status
  const [whisperText, setWhisperText] = useState<string>('');
  const [isSending, setIsSending] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // References to active Room instance and attached audio elements
  const roomRef = useRef<Room | null>(null);
  const audioElementsRef = useRef<Map<string, HTMLAudioElement>>(new Map());

  // 1. Fetch active calls from API via services/api
  const loadActiveCalls = useCallback(async () => {
    setIsFetchingCalls(true);
    try {
      const data = await fetchActiveCalls();
      setActiveCalls(data);
    } catch (err: unknown) {
      console.warn('Failed to fetch active calls:', err);
    } finally {
      setIsFetchingCalls(false);
    }
  }, []);

  // Poll for active calls every 3 seconds when idle
  useEffect(() => {
    if (connectionState === 'idle') {
      loadActiveCalls();
      const interval = setInterval(loadActiveCalls, 3000);
      return () => clearInterval(interval);
    }
  }, [connectionState, loadActiveCalls]);

  // Helper to re-evaluate remote participants
  const updateParticipantStatus = useCallback((room: Room) => {
    const remotes = Array.from(room.remoteParticipants.values());

    // Identify agent participant strictly by ParticipantKind.AGENT
    const foundAgent = remotes.find((p) => p.kind === ParticipantKind.AGENT);
    if (foundAgent) {
      setAgentPresent(true);
      setAgentIdentity(foundAgent.identity);
    } else {
      setAgentPresent(false);
      setAgentIdentity(null);
    }

    // Identify customer participant
    const foundCustomer = remotes.find((p) => p.identity.startsWith('customer-'));
    setCustomerPresent(Boolean(foundCustomer));
  }, []);

  // 2. Disconnect and cleanup function
  const handleLeaveCall = useCallback(async () => {
    if (roomRef.current) {
      roomRef.current.disconnect();
      roomRef.current = null;
    }

    // Detach and remove all audio elements
    audioElementsRef.current.forEach((el) => {
      el.pause();
      el.remove();
    });
    audioElementsRef.current.clear();

    setMonitoredCallId(null);
    setConnectionState('idle');
    setAgentPresent(false);
    setAgentIdentity(null);
    setCustomerPresent(false);
    setStatusMessage(null);
    setErrorMessage(null);
    setWhisperText('');
  }, []);

  // Clean up on component unmount
  useEffect(() => {
    return () => {
      if (roomRef.current) {
        roomRef.current.disconnect();
      }
      audioElementsRef.current.forEach((el) => el.remove());
      audioElementsRef.current.clear();
    };
  }, []);

  // 3. Connect to call as hidden supervisor/operator
  const handleJoinCall = async (callId: string) => {
    setErrorMessage(null);
    setStatusMessage(null);
    setConnectionState('connecting');
    setMonitoredCallId(callId);

    try {
      // Obtain operator JWT token from API via services/api
      const tokenData = await getOperatorToken(callId);

      // Create LiveKit Room instance (strictly listen-only: NO audioCaptureDefaults)
      const room = new Room({
        dynacast: true,
      });
      roomRef.current = room;

      // Track subscribed audio from customer and agent to listen in
      room.on(
        RoomEvent.TrackSubscribed,
        (track: RemoteTrack, _publication: RemoteTrackPublication, participant: RemoteParticipant) => {
          if (track.kind === Track.Kind.Audio) {
            const trackSid = track.sid || `track-${participant.identity}-${Date.now()}`;
            const el = track.attach();
            el.id = `audio-${participant.identity}-${trackSid}`;
            document.body.appendChild(el);
            audioElementsRef.current.set(trackSid, el);
          }
        }
      );

      // Track unsubscribed cleanup
      room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
        if (!track.sid) return;
        const el = audioElementsRef.current.get(track.sid);
        if (el) {
          track.detach(el);
          el.remove();
          audioElementsRef.current.delete(track.sid);
        }
      });

      // Participant connectivity changes
      room.on(RoomEvent.ParticipantConnected, () => updateParticipantStatus(room));
      room.on(RoomEvent.ParticipantDisconnected, () => updateParticipantStatus(room));

      room.on(RoomEvent.Disconnected, () => {
        handleLeaveCall();
      });

      // Connect to LiveKit server with the operator token
      await room.connect(tokenData.livekit_url, tokenData.token);

      // Unlock browser audio playback so operator can hear both parties
      await room.startAudio();

      setConnectionState('connected');
      updateParticipantStatus(room);
    } catch (err: unknown) {
      console.error('Failed to join call as operator:', err);
      setErrorMessage(err instanceof Error ? err.message : 'Failed to join call');
      setConnectionState('idle');
      setMonitoredCallId(null);
    }
  };

  // 4. Send Whisper: Fail-closed enforcement
  const handleSendWhisper = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setStatusMessage(null);

    const room = roomRef.current;
    if (!room) {
      setErrorMessage('Not connected to a room');
      return;
    }

    // Fail closed: re-verify agent participant identity exists
    const agentP = Array.from(room.remoteParticipants.values()).find(
      (p) => p.kind === ParticipantKind.AGENT
    );
    const targetIdentity = agentP?.identity || agentIdentity;

    if (!targetIdentity) {
      setErrorMessage('Agent participant not found in room; whisper aborted.');
      return;
    }

    const trimmedText = whisperText.trim();
    if (!trimmedText) {
      return;
    }

    setIsSending(true);
    try {
      const payload = new TextEncoder().encode(JSON.stringify({ text: trimmedText }));

      // NEVER call publishData without destinationIdentities
      await room.localParticipant.publishData(payload, {
        reliable: true,
        topic: 'operator.whisper',
        destinationIdentities: [targetIdentity],
      });

      setWhisperText('');
      setStatusMessage('Whisper delivered to agent.');
    } catch (err: unknown) {
      console.error('Failed to send whisper:', err);
      setErrorMessage(err instanceof Error ? err.message : 'Failed to send whisper');
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="operator-page">
      <h2>Supervisor Call Monitoring & Whisper</h2>
      <p className="page-description">
        Join active calls invisibly as a hidden listener. Send private guidance to the agent
        without the customer seeing or hearing your guidance.
      </p>

      {errorMessage && <div className="error-banner">{errorMessage}</div>}
      {statusMessage && <div className="status-banner">{statusMessage}</div>}

      {/* Call Selector (when not connected) */}
      {connectionState === 'idle' && (
        <section className="active-calls-section">
          <div className="section-header">
            <h3>Active Calls</h3>
            <button
              onClick={loadActiveCalls}
              disabled={isFetchingCalls}
              className="refresh-button"
            >
              {isFetchingCalls ? 'Refreshing...' : 'Refresh'}
            </button>
          </div>

          {activeCalls.length === 0 ? (
            <p className="no-calls-message">
              No active calls right now. Start a call on the Customer page first.
            </p>
          ) : (
            <table className="calls-table">
              <thead>
                <tr>
                  <th>Call ID</th>
                  <th>Agent</th>
                  <th>Room</th>
                  <th>Started At</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {activeCalls.map((call) => (
                  <tr key={call.id}>
                    <td>
                      <code>{call.id.slice(0, 8)}...</code>
                    </td>
                    <td>{call.agent_name || 'Acme Agent'}</td>
                    <td>{call.room_name}</td>
                    <td>{call.started_at ? new Date(call.started_at).toLocaleTimeString() : 'Starting...'}</td>
                    <td>
                      <button
                        onClick={() => handleJoinCall(call.id)}
                        className="monitor-button"
                      >
                        Monitor Call
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {/* Connecting State */}
      {connectionState === 'connecting' && (
        <div className="connecting-banner">
          Connecting to room invisibly as supervisor...
        </div>
      )}

      {/* Connected Monitor View */}
      {connectionState === 'connected' && (
        <section className="monitor-section">
          <div className="monitor-header">
            <div>
              <h3>Monitoring Call: <code>{monitoredCallId?.slice(0, 8)}</code></h3>
              <div className="participant-badges">
                <span className={`badge ${customerPresent ? 'badge-green' : 'badge-gray'}`}>
                  Customer: {customerPresent ? 'Online' : 'Waiting...'}
                </span>
                <span className={`badge ${agentPresent ? 'badge-green' : 'badge-red'}`}>
                  Agent: {agentPresent ? `Connected (${agentIdentity})` : 'Not Found'}
                </span>
                <span className="badge badge-purple">
                  Operator: Hidden (Listen-Only)
                </span>
              </div>
            </div>
            <button onClick={handleLeaveCall} className="leave-button">
              Leave Call
            </button>
          </div>

          {/* Whisper Form: Fail-Closed Enforced */}
          <form onSubmit={handleSendWhisper} className="whisper-form">
            <label htmlFor="whisper-input">
              Private Guidance (Whisper to Agent):
            </label>
            <div className="whisper-input-row">
              <input
                id="whisper-input"
                type="text"
                placeholder={
                  agentPresent
                    ? "e.g., Offer a 10% loyalty discount"
                    : "Agent not found; whisper disabled"
                }
                value={whisperText}
                onChange={(e) => setWhisperText(e.target.value)}
                disabled={!agentPresent || isSending}
              />
              <button
                type="submit"
                disabled={!agentPresent || !whisperText.trim() || isSending}
                className="whisper-send-button"
              >
                {isSending ? 'Sending...' : 'Send Whisper'}
              </button>
            </div>
            {!agentPresent && (
              <p className="field-hint warning">
                Whisper is disabled because no participant with ParticipantKind.AGENT is present in this room.
              </p>
            )}
            {agentPresent && (
              <p className="field-hint">
                Sent privately to agent identity <code>{agentIdentity}</code> via LiveKit data channel.
              </p>
            )}
          </form>
        </section>
      )}
    </div>
  );
};
