import React from 'react';
import { Link } from 'react-router-dom';

export const OperatorPlaceholderPage: React.FC = () => {
  return (
    <div className="card" style={{ marginTop: '20px' }}>
      <h2 style={{ fontSize: '1.2rem', marginBottom: '10px' }}>Operator View</h2>
      <p style={{ color: '#9ca3af', marginBottom: '14px' }}>
        The Operator interface (supervisor audio listening, live transcription, and silent operator
        whispers) will be implemented in <strong>Phase 5</strong>.
      </p>
      <div>
        <Link to="/" style={{ color: '#60a5fa', textDecoration: 'underline' }}>
          &larr; Return to Customer Call page
        </Link>
      </div>
    </div>
  );
};
