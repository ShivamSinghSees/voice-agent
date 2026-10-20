---
name: livekit-agents-reference
description: Use before writing or changing any code that touches LiveKit Agents, LiveKit server APIs, tokens, data channels, egress, or the livekit client SDKs.
---

# LiveKit Agents Reference

Follow these steps before writing or modifying any LiveKit-related code:

1. **Verify Installed Versions First**:
   - Check Python agent SDK: `pip show livekit-agents` (or via `uv pip show livekit-agents`).
   - Check Node client SDK: `npm ls livekit-client` or check `web/package.json`.
2. **Consult Matching Documentation**:
   - Read the exact docs matching the installed major/minor version at `docs.livekit.io` (Agents framework, self-hosting deployment, data messages, and token grant specs).
   - Review reference implementations in `github.com/livekit/agents` repository and its `examples/` directory for the matching version tag.
3. **Copy Current Patterns Only**:
   - Do not rely on memory or older versions of the SDK. APIs in LiveKit Agents evolve rapidly between releases.
   - Use explicit agent dispatch (agent name + job metadata) rather than implicit auto-dispatch unless explicitly overridden for a test phase.
4. **Document References**:
   - Note the doc URL and the package version used in a code comment or in `LEARNING_NOTES.md`.
5. **Source Code is Ground Truth**:
   - If documentation and installed library behaviour disagree, inspect the installed package source code directly.
   - Report any discrepancy explicitly to the human operator before proceeding.
