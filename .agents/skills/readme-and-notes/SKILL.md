---
name: readme-and-notes
description: Use when writing README sections or appending to LEARNING_NOTES.md.
---

# README and Learning Notes

Guidelines for maintaining documentation and developer notes:

1. **LEARNING_NOTES.md Entry Requirements**:
   - Append an entry after each phase with the following structure:
     - **(a) What was built**: Concrete summary of components, routes, or modules added/modified.
     - **(b) How the underlying LiveKit/WebRTC mechanism works**: Plain-language explanation of technical internals (e.g., token grants, room events, data channel broadcasts, ICE candidate gathering, audio track subscription).
     - **(c) Files and functions to read first**: Curated list of entry points and key functions to help a reviewer quickly orient themselves.
     - **(d) Likely reviewer questions & short answers**: 2 to 3 anticipated technical questions with direct, articulate explanations.
     - **(e) Decisions & tradeoffs**: Key architectural choices made and alternative paths evaluated.
     - **(f) Known gaps**: Explicitly list unaddressed edge cases or deferred requirements.

2. **Root README.md Standards**:
   - Must be factual, reproducible from scratch, and free of speculative claims.
   - Maintain the following core sections:
     - **Setup**: Exact prerequisite versions and setup commands.
     - **Architecture**: Clear architecture diagram (Mermaid or linked asset) demonstrating API, Agent, Web, and LiveKit data/media flow.
     - **Ports and LiveKit Config**: Table of exposed ports and their technical purpose (HTTP signalling, WebRTC TCP fallback, UDP media port range).
     - **Latency**: Measured latency table populated strictly with real-world turn metrics.
     - **Known Limitations & Next Steps**: Honest breakdown of POC boundaries.
   - **Never put unverified claims or placeholder measurements in the README.**
