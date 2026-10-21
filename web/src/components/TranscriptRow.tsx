import React from 'react';
import { LiveTranscriptItem } from '../types';

export interface TranscriptRowProps {
  item: LiveTranscriptItem;
}

export const TranscriptRow: React.FC<TranscriptRowProps> = React.memo(({ item }) => {
  return (
    <div className={`transcript-line ${item.speaker === 'You' ? 'you' : 'agent'}`}>
      <span
        className={`speaker-label ${item.speaker === 'You' ? 'you-label' : 'agent-label'}`}
      >
        [{item.speaker}]
      </span>
      <span>{item.text}</span>
      {!item.isFinal && (
        <span className="interim-indicator" aria-hidden="true">
          ...
        </span>
      )}
    </div>
  );
});

TranscriptRow.displayName = 'TranscriptRow';
