import React, { useEffect, useId, useRef } from 'react';

export interface LeaveCallDialogProps {
  open: boolean;
  /** Fired for every close: Stay button, Esc, backdrop click, or programmatic close. */
  onClose: () => void;
  onOpenNewTab: () => void;
  onEndAndSwitch: () => void;
}

export const LeaveCallDialog: React.FC<LeaveCallDialogProps> = ({
  open,
  onClose,
  onOpenNewTab,
  onEndAndSwitch,
}) => {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const primaryRef = useRef<HTMLButtonElement | null>(null);
  const titleId = useId();

  // Native <dialog>: showModal() provides the focus trap and Esc-to-close.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      dialog.showModal();
      primaryRef.current?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className="leave-dialog"
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => {
        // Clicks on the backdrop target the <dialog> element itself.
        if (e.target === dialogRef.current) onClose();
      }}
    >
      <div className="leave-dialog-body">
        <h2 id={titleId} className="leave-dialog-title">
          Call in progress
        </h2>
        <p className="leave-dialog-text">
          A call is in progress. Leaving this page will end it. To monitor as an operator, open the
          Operator View in a new tab and mute that tab (right-click the tab, then &lsquo;Mute
          site&rsquo;). Otherwise you will hear the call twice.
        </p>
        <div className="leave-dialog-actions">
          <button
            type="button"
            ref={primaryRef}
            className="btn-call"
            onClick={onOpenNewTab}
          >
            Open in new tab
          </button>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Stay on call
          </button>
          <button type="button" className="btn-hangup" onClick={onEndAndSwitch}>
            End call and switch
          </button>
        </div>
      </div>
    </dialog>
  );
};
