---
name: latency-measurement
description: Use when adding or changing per-turn timing/latency logging or the README latency table.
---

# Latency Measurement

Guidelines for per-turn latency logging and reporting:

1. **Structured Per-Turn Logging**:
   - For every agent turn, log exactly one structured JSON line.
   - Record monotonic-clock stage timestamps in milliseconds:
     - `turn_id`: Unique identifier for the conversation turn.
     - `user_stopped_speaking`: Timestamp when VAD signals end-of-speech.
     - `stt_final`: Timestamp when the final STT transcription is emitted.
     - `llm_first_token`: Timestamp when the first text token is received from the LLM.
     - `tts_first_audio`: Timestamp when the first synthesized audio chunk is generated.
     - `audio_published`: Timestamp when the first synthesized audio frame is published to the room.
     - `deltas`: Stage-to-stage elapsed times in ms (e.g., VAD-to-STT, STT-to-LLM-1st-token, LLM-to-TTS-audio, TTS-to-publish, total turn latency).

2. **Metrics Source & Clock Integrity**:
   - Prefer LiveKit framework built-in metrics and events (verify current metric names in installed package docs).
   - Supplement any framework gaps with explicit monotonic timestamps (`time.monotonic()` in Python).
   - Document clearly in code comments what each timestamp signifies and which clock is referenced.

3. **Strict Truthfulness**:
   - Never fabricate, estimate, round up, or smooth numbers.
   - Log null if a stage is not observable, never fabricate. Always explain why the stage was unobservable in documentation or logs.

4. **Markdown Table Generation**:
   - Provide a lightweight script (e.g., in `agent/` or `scripts/`) that processes the structured log lines.
   - Compute min, median, and max latency across real turns ($n$ turns).
   - Output the formatted Markdown table directly for inclusion in `README.md`.
