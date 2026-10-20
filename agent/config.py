"""Configuration settings for LiveKit voice agent worker.

Loads configuration from root .env using an explicit absolute filesystem path
to prevent working directory dependency issues.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly load from repository root .env
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT_DIR / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

# LiveKit connection
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

# Voice Pipeline Providers
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# API Backend coordination
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "")

# Worker target pool
AGENT_NAME = "support-agent"
