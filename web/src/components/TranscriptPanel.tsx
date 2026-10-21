import React, { useRef, useState, useEffect } from 'react';
import { LiveTranscriptItem, CallState } from '../types';
import { TranscriptRow } from './TranscriptRow';

export interface TranscriptPanelProps {
  items: LiveTranscriptItem[];
  callState: CallState;
}

export const TranscriptPanel: React.FC<TranscriptPanelProps> = ({ items, callState }) => {
  const transcriptBoxRef = useRef<HTMLDivElement | null>(null);
  const isNearBottomRef = useRef<boolean>(true);
  const [showJumpToLatest, setShowJumpToLatest] = useState<boolean>(false);

  // Update nearBottom tracking ref when user scrolls the transcript box
  const handleScroll = () => {
    const el = transcriptBoxRef.current;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    const nearBottom = distanceToBottom < 80;
    isNearBottomRef.current = nearBottom;
    if (nearBottom) {
      setShowJumpToLatest(false);
    }
  };

  // Scroll to bottom and restore stick-to-bottom mode
  const handleJumpToLatest = () => {
    const el = transcriptBoxRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
      isNearBottomRef.current = true;
      setShowJumpToLatest(false);
    }
  };

  // Stick-to-bottom scroll effect when near bottom, otherwise prompt jump-to-latest
  useEffect(() => {
    if (items.length === 0) {
      isNearBottomRef.current = true;
      setShowJumpToLatest(false);
      return;
    }

    const el = transcriptBoxRef.current;
    if (!el) return;
    if (isNearBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    } else {
      setShowJumpToLatest(true);
    }
  }, [items]);

  return (
    <div className="card transcript-section">
      <div className="transcript-header">
        <span>Live Conversation Transcript</span>
        {callState === 'in_call' && <span className="state-tag">Active Stream</span>}
      </div>

      <div
        className="transcript-box"
        ref={transcriptBoxRef}
        onScroll={handleScroll}
        role="log"
        aria-label="Live conversation transcript"
      >
        {items.length === 0 ? (
          <div className="empty-transcript">
            {callState === 'in_call'
              ? 'Connected. Start speaking into your microphone...'
              : 'No active call. Select an agent and click "Call Agent" to begin.'}
          </div>
        ) : (
          items.map((item) => <TranscriptRow key={item.id} item={item} />)
        )}
      </div>

      {showJumpToLatest && (
        <button
          type="button"
          className="jump-to-latest-btn"
          onClick={handleJumpToLatest}
          aria-label="Jump to latest transcript"
        >
          &darr; Jump to latest
        </button>
      )}
    </div>
  );
};
