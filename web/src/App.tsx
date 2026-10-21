import React, { useRef, useState } from 'react';
import { Routes, Route, NavLink, useNavigate } from 'react-router-dom';
import { CustomerCallPage } from './pages/CustomerCallPage';
import { OperatorPage } from './pages/OperatorPage';
import { LeaveCallDialog } from './components/LeaveCallDialog';
import { useCallGuard } from './context/CallGuardContext';

export const App: React.FC = () => {
  const { isCallActive, endCall } = useCallGuard();
  const navigate = useNavigate();
  const [leaveDialogOpen, setLeaveDialogOpen] = useState<boolean>(false);
  const operatorLinkRef = useRef<HTMLAnchorElement | null>(null);
  // False only while "End call and switch" runs, so focus is not pulled back to the nav link.
  const restoreFocusRef = useRef<boolean>(true);

  const handleOperatorClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (!isCallActive) return; // No call: behaves exactly as before.
    // Let the browser handle modified clicks (they open a new tab/window natively).
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    setLeaveDialogOpen(true);
  };

  const handleDialogClosed = () => {
    setLeaveDialogOpen(false);
    if (restoreFocusRef.current) {
      operatorLinkRef.current?.focus();
    }
    restoreFocusRef.current = true;
  };

  const handleOpenNewTab = () => {
    window.open('/operator', '_blank', 'noopener,noreferrer');
    setLeaveDialogOpen(false);
  };

  const handleEndAndSwitch = () => {
    restoreFocusRef.current = false;
    setLeaveDialogOpen(false);
    endCall(); // Existing hang-up path (disconnects the room once).
    navigate('/operator');
  };

  return (
    <div className="app-container">
      <header>
        <h1>Voice AI Agent</h1>
        <nav>
          <NavLink to="/" className={({ isActive }) => (isActive ? 'active' : '')}>
            Customer Call
          </NavLink>
          <NavLink
            to="/operator"
            ref={operatorLinkRef}
            onClick={handleOperatorClick}
            className={({ isActive }) => (isActive ? 'active' : '')}
          >
            Operator View
          </NavLink>
        </nav>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<CustomerCallPage />} />
          <Route path="/operator" element={<OperatorPage />} />
        </Routes>
      </main>

      <LeaveCallDialog
        open={leaveDialogOpen}
        onClose={handleDialogClosed}
        onOpenNewTab={handleOpenNewTab}
        onEndAndSwitch={handleEndAndSwitch}
      />
    </div>
  );
};
