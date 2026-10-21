import { useState, useRef, useCallback, useEffect } from 'react';
import { Room, ParticipantKind } from 'livekit-client';
import { LiveTranscriptItem } from '../types';

type TextStreamHandler = Parameters<Room['registerTextStreamHandler']>[1];
type TextStreamReader = Parameters<TextStreamHandler>[0];
type ParticipantInfo = Parameters<TextStreamHandler>[1];

export interface UseLiveTranscriptReturn {
  lines: LiveTranscriptItem[];
  attachRoom: (room: Room) => void;
  detachRoom: () => void;
  clearTranscript: () => void;
}

/**
 * Hook to manage live text streaming transcription from LiveKit room.
 * Batches incoming chunks with requestAnimationFrame (~60fps) and maintains
 * immutable item references so unchanged transcript rows skip re-rendering.
 */
export function useLiveTranscript(initialRoom: Room | null = null): UseLiveTranscriptReturn {
  const [lines, setLines] = useState<LiveTranscriptItem[]>([]);

  const roomRef = useRef<Room | null>(null);
  const segmentsRef = useRef<Map<string, LiveTranscriptItem>>(new Map());
  const orderRef = useRef<string[]>([]);
  const itemsRef = useRef<LiveTranscriptItem[]>([]);
  const rafIdRef = useRef<number | null>(null);
  const generationRef = useRef<number>(0);
  const loggedMissingIdRef = useRef<boolean>(false);

  // Synchronous flush of latest segments to React state
  const flush = useCallback(() => {
    const prev = itemsRef.current;
    const prevIdSet = new Set(prev.map((it) => it.id));
    let hasChanged = false;

    // Preserve existing object references for all unchanged lines
    const next: LiveTranscriptItem[] = prev.map((item) => {
      const latest = segmentsRef.current.get(item.id);
      if (latest && (latest.text !== item.text || latest.isFinal !== item.isFinal)) {
        hasChanged = true;
        return latest; // New object reference ONLY for the modified line
      }
      return item; // Unchanged reference for historical lines
    });

    // Append newly arrived segments in their recorded arrival order
    for (const id of orderRef.current) {
      if (!prevIdSet.has(id)) {
        const newItem = segmentsRef.current.get(id);
        if (newItem) {
          next.push(newItem);
          hasChanged = true;
        }
      }
    }

    if (hasChanged) {
      itemsRef.current = next;
      setLines(next);
    }
  }, []);

  // Coalesce updates to animation frame (~60fps)
  const scheduleFlush = useCallback(() => {
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(() => {
        rafIdRef.current = null;
        flush();
      });
    }
  }, [flush]);

  // Clears all state and in-flight stream processing
  const clearTranscript = useCallback(() => {
    generationRef.current += 1;
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    segmentsRef.current.clear();
    orderRef.current = [];
    itemsRef.current = [];
    setLines([]);
  }, []);

  // Text stream reader consumer
  const handleTextStream: TextStreamHandler = useCallback(
    async (reader: TextStreamReader, participantInfo: ParticipantInfo) => {
      const room = roomRef.current;
      if (!room) return;

      const sender = participantInfo.identity;
      const remoteParticipant = room.remoteParticipants.get(sender);

      const isUser =
        sender === room.localParticipant.identity || sender.startsWith('customer-');
      const isAgent =
        (remoteParticipant && remoteParticipant.kind === ParticipantKind.AGENT) ||
        sender.startsWith('agent-');

      if (!isUser && !isAgent) {
        return;
      }

      const speaker: 'You' | 'Agent' = isUser ? 'You' : 'Agent';
      const segmentId = reader.info.attributes?.['lk.segment_id'] || reader.info.id;
      if (!segmentId) {
        if (!loggedMissingIdRef.current) {
          console.warn('[TRANSCRIPTION] Stream received without segment_id or stream id; ignoring');
          loggedMissingIdRef.current = true;
        }
        return;
      }

      const isFinalAttribute = reader.info.attributes?.['lk.transcription_final'] === 'true';
      const streamGen = generationRef.current;

      let accumulatedText = '';
      const startTimestamp = performance.now();

      try {
        for await (const chunk of reader) {
          // Check generation before writing to prevent stale writes across calls
          if (generationRef.current !== streamGen) {
            return;
          }
          accumulatedText += chunk;

          const existing = segmentsRef.current.get(segmentId);
          if (existing) {
            const updatedItem: LiveTranscriptItem = {
              ...existing,
              text: accumulatedText,
            };
            segmentsRef.current.set(segmentId, updatedItem);
          } else {
            const newItem: LiveTranscriptItem = {
              id: segmentId,
              speaker,
              text: accumulatedText,
              isFinal: isFinalAttribute,
              timestamp: startTimestamp,
            };
            orderRef.current.push(segmentId);
            segmentsRef.current.set(segmentId, newItem);
          }

          scheduleFlush();
        }

        // Stream completed
        if (generationRef.current !== streamGen) {
          return;
        }

        // 1. Update segmentsRef (isFinal: true, full text) FIRST
        const existing = segmentsRef.current.get(segmentId);
        if (existing) {
          const finalizedItem: LiveTranscriptItem = {
            ...existing,
            text: accumulatedText,
            isFinal: true,
          };
          segmentsRef.current.set(segmentId, finalizedItem);
        }

        // 2. Cancel pending frame
        if (rafIdRef.current !== null) {
          cancelAnimationFrame(rafIdRef.current);
          rafIdRef.current = null;
        }

        // 3. Flush synchronously
        flush();
      } catch (err: unknown) {
        console.warn(`[TRANSCRIPTION STREAM ERROR] sender=${sender} | segment=${segmentId}:`, err);
      }
    },
    [flush, scheduleFlush]
  );

  // Synchronously detach from current room and unregister handler
  const detachRoom = useCallback(() => {
    generationRef.current += 1;
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    const r = roomRef.current;
    if (r) {
      try {
        r.unregisterTextStreamHandler('lk.transcription');
      } catch {
        // safe ignore if room already destroyed
      }
      roomRef.current = null;
    }
  }, []);

  // Synchronously attach to room BEFORE room.connect()
  const attachRoom = useCallback(
    (room: Room) => {
      detachRoom();
      roomRef.current = room;
      generationRef.current += 1;
      clearTranscript();
      room.registerTextStreamHandler('lk.transcription', handleTextStream);
    },
    [detachRoom, clearTranscript, handleTextStream]
  );

  // Cleanup on unmount or when initialRoom changes
  useEffect(() => {
    if (initialRoom) {
      attachRoom(initialRoom);
    }
    return () => {
      detachRoom();
    };
  }, [initialRoom, attachRoom, detachRoom]);

  return {
    lines,
    attachRoom,
    detachRoom,
    clearTranscript,
  };
}
