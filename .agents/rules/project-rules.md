# Project rules

## Scope
- Proof of concept. Working end to end beats polish. No premature abstraction, no extra dependencies without a reason noted in LEARNING_NOTES.md.
- The human must explain every line. Prefer plain, small functions. Comment WHY for any LiveKit/WebRTC-specific line (tokens, permissions, dispatch, data channels, turn handling).

## Architecture (keep strictly separated)
- `api/`: FastAPI app only. Owns DB access via a single `storage` module, token issuance, call lifecycle, compliance endpoints.
- `agent/`: LiveKit Agents worker only. No HTTP server of its own. Talks to the API over HTTP (or through the same storage module, decided in phase 3 and recorded in LEARNING_NOTES.md). Never import code from `api/` internals.
- `web/`: React (Vite) app with two routes: customer page and operator page.
- `infra/`: livekit.yaml, docker-compose, env templates.
- Secrets only in `.env` (gitignored); keep `.env.example` up to date. Never commit keys.

## LiveKit correctness
- BEFORE using any LiveKit Agents / server SDK / client SDK API, check current docs (docs.livekit.io) and examples (github.com/livekit/agents, livekit/agents examples folder) for the INSTALLED version. The API changes often. Pin dependency versions and record them. If unsure about an API, say so explicitly and verify; never guess or rely on memory.
- Use explicit agent dispatch (agent name + job metadata) rather than relying on automatic dispatch, unless a phase says otherwise.

## Behaviour invariants (the assignment evaluates these; never regress them)
1. Barge-in: agent stops speaking immediately; conversation history keeps ONLY the text actually spoken before the interruption, not the full generated sentence.
2. The operator is hidden from the customer (hidden participant, no publish rights unless a phase enables take-over). The agent must not treat the operator as a conversation participant.
3. Whispers are delivered via the LiveKit data channel, used by the agent in its next reply, never spoken verbatim, never attributed to an operator/supervisor/manager, and never added to the user-visible transcript.
4. The "recording disclosure within 30 seconds" check is DETERMINISTIC from timestamps. No LLM involved.
5. The "no refund promise" check uses an LLM and must return VALIDATED structured output (pydantic model: pass/fail + offending line or null); handle malformed output with retry/fallback.
6. Latency numbers are MEASURED from real calls with timestamps logged by the code. Never invent, estimate or round up numbers. If a stage cannot be measured, say so.

## Process
- Use Planning mode for multi-file work: produce an implementation plan and WAIT for approval. Fast mode only for small fixes.
- Verify every step by actually running it (server up, curl, tests, logs). Never claim "should work". Real voice/mic testing is done manually by the human; ask for the exact result to be pasted back.
- End every phase with a walkthrough containing REAL evidence (commands run, output, logs, file list, versions) and a list of anything unverified.
- After every phase, append an entry to LEARNING_NOTES.md (see skill readme-and-notes).
- Keep error handling and retries sensible but small (timeouts on HTTP calls, clear error messages).
