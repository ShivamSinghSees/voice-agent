# Workflow: /verify-phase

Use this workflow at the end of each implementation phase to rigorously verify all deliverables against acceptance criteria.

## Verification Procedure

1. **List Promised Deliverables**:
   - Revisit the phase requirements and explicitly list every promised feature, API endpoint, behavior invariant, and artifact.

2. **Start Required Services**:
   - Ensure all necessary services for the phase (e.g., LiveKit server via Docker, FastAPI backend, LiveKit agent worker, React dev server) are running in test or local mode.
   - Confirm healthy startup status through endpoint healthchecks or process state.

3. **Execute Automated & CLI Checks**:
   - Run unit tests and integration tests relevant to the phase.
   - Run targeted `curl` requests or CLI commands for every endpoint and functional criterion.
   - Capture the raw output and exit status for each check.

4. **Inspect Runtime Logs**:
   - Inspect server, agent, and browser/client logs for unexpected warnings, exceptions, or connection failures.
   - Highlight any WebRTC negotiation issues, token permission errors, or unhandled promise rejections.

5. **Report Acceptance Status**:
   - For every criterion, report an explicit status:
     - **PASS**: Verified with matching command output.
     - **FAIL**: Tested and did not meet acceptance criteria.
     - **UNVERIFIED**: Cannot be tested programmatically or requires human interaction.
   - Include the actual terminal commands executed and their direct outputs. Never claim something "should work" without evidence.

6. **Specify Manual Testing Steps**:
   - If audio, microphone, or browser interaction is required, provide step-by-step instructions for the human tester.
   - State precisely what URL to open, what actions to perform (e.g., speaking, interrupting, sending a whisper), and what exact logs or observations to copy and paste back.
