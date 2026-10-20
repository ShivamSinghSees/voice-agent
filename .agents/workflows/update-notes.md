# Workflow: /update-notes

Use this workflow after completing a development phase or milestone to record learnings and technical decisions in `LEARNING_NOTES.md`.

## Procedure

1. **Analyze Real Changes**:
   - Inspect the git diff and review the files created or modified during the phase.
   - Do not rely on memory or assumptions; base all documentation on what is actually present in the codebase.

2. **Compose Dated Entry**:
   - Create a new entry under a dated heading (e.g., `## [YYYY-MM-DD] - Phase N: <Phase Name>`).
   - Strictly follow the structure defined in the `readme-and-notes` skill:
     - **What was built**: Concrete summary of endpoints, schemas, agent modules, UI pages, and infrastructure scripts added or changed.
     - **Underlying LiveKit / WebRTC mechanisms**: Clear explanation of how LiveKit primitives operate in this phase (tokens, room events, data channels, tracks, hidden participant flags, VAD / turn-detector handling).
     - **Files & functions to read first**: Curated list of entry points and important functions to inspect.
     - **Reviewer questions & answers**: 2 to 3 anticipated reviewer questions with crisp, technical answers.
     - **Decisions & tradeoffs**: Alternatives considered, why the chosen path was taken, and why dependencies were added (if any).
     - **Known gaps**: Explicit list of edge cases deferred or limitations in this POC phase.

3. **Append to `LEARNING_NOTES.md`**:
   - Append the new entry at the bottom of `LEARNING_NOTES.md` while preserving earlier entries.
   - Verify that all file links and code references are accurate and formatted correctly.
