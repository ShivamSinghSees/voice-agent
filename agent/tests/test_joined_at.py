"""Unit tests for the customer joined_at timestamp unit/conversion.

Facts verified in the installed packages (livekit-rtc 1.1.20, livekit-api 1.2.2):
- rtc FFI proto `livekit.rtc._proto.participant_pb2.ParticipantInfo` has ONE field, `joined_at`,
  and its value is epoch MILLISECONDS (raw value seen on a live join: 1792512889628).
- `livekit/rtc/participant.py` lines 191-198: `Participant.joined_at` already divides by 1000
  and returns a tz-aware UTC datetime (or None when the raw value is 0).
- The server API proto `livekit.api.ParticipantInfo` has BOTH `joined_at` (SECONDS, e.g. 1792512889)
  and `joined_at_ms` (MILLISECONDS). agent.py uses the rtc property, so no manual division is done there.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from livekit import rtc

from agent import _format_iso

# Real raw value captured from the self-hosted server for a live participant (rtc _info.joined_at).
REAL_RAW_JOINED_AT_MS = 1792512889628


def _sdk_joined_at(raw_ms: int):
    """Invoke the REAL installed Participant.joined_at property on a stub holding `_info`."""
    stub = SimpleNamespace(_info=SimpleNamespace(joined_at=raw_ms))
    return rtc.Participant.joined_at.fget(stub)


def test_sdk_joined_at_converts_milliseconds_to_utc_datetime():
    dt = _sdk_joined_at(REAL_RAW_JOINED_AT_MS)
    assert isinstance(dt, datetime)
    assert dt.tzinfo == timezone.utc
    assert (dt.year, dt.month, dt.day) == (2026, 10, 20)
    assert _format_iso(dt) == "2026-10-20T16:14:49.628Z"


def test_sdk_joined_at_zero_is_none_not_1970():
    assert _sdk_joined_at(0) is None


def test_dividing_seconds_by_1000_would_give_1970():
    """Documents the bug we must avoid: api.ParticipantInfo.joined_at is SECONDS."""
    api_joined_at_seconds = 1792512889
    wrong = datetime.fromtimestamp(api_joined_at_seconds / 1000, tz=timezone.utc)
    assert wrong.year == 1970
    right = datetime.fromtimestamp(api_joined_at_seconds, tz=timezone.utc)
    assert right.year == 2026
