---
name: compliance-check
description: Use when implementing or changing post-call transcript storage or compliance rules.
---

# Compliance Check

Specifications for transcript storage and compliance evaluation:

1. **Transcript Row Schema**:
   - Store transcript entries as:
     `(call_id, seq, speaker, text, started_at, ended_at)`
   - `speaker` must be strictly in `{"agent", "user"}`.
   - Timestamps must be recorded as ISO UTC timestamps and also measured relative to call start offset (in seconds).

2. **Rule 1: Recording Disclosure within 30 Seconds**:
   - **Deterministic Python logic**: Do NOT use an LLM for this rule.
   - Evaluate over the transcript rows to find the first line spoken by `agent` matching a documented set of phrases/regex related to call recording (e.g., "call is recorded", "recording this call").
   - Check that its `started_at` offset is `<= 30.0` seconds from call start.
   - Return `{passed: bool, matched_line: str | null, offset_seconds: float | null}`.

3. **Rule 2: No Refund Promise**:
   - Use an LLM call instructed to detect any agent promise of a refund.
   - Response must be strictly validated against a Pydantic schema:
     ```python
     class RefundCheckResult(BaseModel):
         passed: bool
         offending_line: str | None = None
     ```
   - On schema validation failure or malformed JSON, retry once with an explicit correction prompt.
   - If retry fails, record an `"error"` status in the database instead of raising an unhandled exception or crashing the pipeline.

4. **Required Unit-Test Edge Cases**:
   - Test Rule 1:
     - Disclosure at `29.9s` -> PASS.
     - Disclosure at `30.1s` -> FAIL.
     - Disclosure completely absent -> FAIL.
     - Disclosure phrase spoken by the `user` rather than the `agent` -> FAIL.
   - Test Rule 2:
     - Explicit refund promise ("I will process your refund today") -> FAIL.
     - Conditional or negated statements ("We cannot issue a refund under our policy", "If approved by management, a refund might be considered") -> verify correct classification according to policy.

5. **Storage and Exposure**:
   - Results are persisted alongside the call record in the database.
   - Exposed to clients and operators via `GET /calls/{id}`.
