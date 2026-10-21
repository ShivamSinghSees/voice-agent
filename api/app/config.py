"""Configuration settings for the FastAPI backend service.

Loads environment variables from the repository root .env by explicit absolute path.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve repository root explicitly to avoid CWD-dependent behavior
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOTENV_PATH = REPO_ROOT / ".env"

if DOTENV_PATH.exists():
    load_dotenv(dotenv_path=DOTENV_PATH)

# LiveKit Server Configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# Voice Pipeline Provider API Keys
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Database Configuration (resolved relative to REPO_ROOT if relative)
_db_raw = os.getenv("DATABASE_PATH", "./voice_agent.db")
DATABASE_PATH = str(REPO_ROOT / _db_raw) if not os.path.isabs(_db_raw) else _db_raw

# Internal Security Token for Worker-to-API communication
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

# CORS Configuration
_cors_raw = os.getenv("CORS_ORIGINS", "http://localhost:5173")
CORS_ORIGINS = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]

# Constant Agent Name for Explicit Dispatch in LiveKit
# The Python LiveKit worker in Phase 3 registers with this exact name.
AGENT_NAME = "support-agent"

# LLM Model used for Post-Call Compliance Checking
COMPLIANCE_MODEL = os.getenv("COMPLIANCE_MODEL", "qwen/qwen3.8-27b")
