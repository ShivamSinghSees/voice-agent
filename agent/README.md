# Voice Agent Service

LiveKit Agents Python worker for real-time voice interaction.

## Purpose & Scope
- Implements the real-time voice pipeline: VAD -> STT -> LLM -> TTS -> Audio egress.
- Handles turn detection, immediate barge-in truncation, and operator whisper messages over LiveKit data channels.
- Emits per-turn monotonic timestamp logs for latency measurements.
- Runs purely as a LiveKit agent worker without an embedded HTTP server; interfaces with the API service via HTTP or storage abstraction.
