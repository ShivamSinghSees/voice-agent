# API Service

FastAPI backend application for the operator-guided voice AI agent POC.

## Purpose & Scope
- Exposes REST endpoints for call lifecycle management, token issuance, and post-call compliance verification.
- Owns persistent data storage via a dedicated `storage` module.
- Does not contain LiveKit worker process logic or React frontend code.
