import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

interface CallGuardContextValue {
  /** True while a call is connecting or in progress. */
  isCallActive: boolean;
  /** Called by the call page to publish its active state. */
  setCallActive: (active: boolean) => void;
  /** Called by the call page to register (or clear with null) its hang-up function. */
  registerEndCall: (fn: (() => void) | null) => void;
  /** Runs the registered hang-up function, if any. */
  endCall: () => void;
}

const CallGuardContext = createContext<CallGuardContextValue | null>(null);

export const CallGuardProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isCallActive, setIsCallActive] = useState<boolean>(false);
  const endCallRef = useRef<(() => void) | null>(null);

  const registerEndCall = useCallback((fn: (() => void) | null) => {
    endCallRef.current = fn;
  }, []);

  const endCall = useCallback(() => {
    endCallRef.current?.();
  }, []);

  // Covers refresh / tab close / typed URL. Browsers show their own generic text and
  // ignore any custom message. Registered only while a call is active and removed in
  // cleanup, so it is safe under StrictMode's mount -> unmount -> mount.
  useEffect(() => {
    if (!isCallActive) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [isCallActive]);

  const value = useMemo<CallGuardContextValue>(
    () => ({ isCallActive, setCallActive: setIsCallActive, registerEndCall, endCall }),
    [isCallActive, registerEndCall, endCall]
  );

  return <CallGuardContext.Provider value={value}>{children}</CallGuardContext.Provider>;
};

export function useCallGuard(): CallGuardContextValue {
  const ctx = useContext(CallGuardContext);
  if (!ctx) {
    throw new Error('useCallGuard must be used inside <CallGuardProvider>');
  }
  return ctx;
}
