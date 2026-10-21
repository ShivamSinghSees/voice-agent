#!/usr/bin/env python3
"""Parse agent execution logs to generate real per-stage latency reports.

Dedupes records strictly by (call_id, turn_idx). Excludes greeting from turn medians.
Computes min, median, p95, and max for all stages.
Reports AgentSession runtime configuration arguments.
"""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any


def percentile(data: list[float], p: float) -> float:
    """Calculate the p-th percentile of a list of numbers using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    if len(sorted_data) == 1:
        return sorted_data[0]
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    d0 = sorted_data[int(f)] * (c - k)
    d1 = sorted_data[int(c)] * (k - f)
    return d0 + d1


def compute_stats(values: list[float]) -> dict[str, float]:
    """Compute count, min, median, p95, and max for a numeric series."""
    if not values:
        return {"count": 0, "min": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "min": round(min(values), 2),
        "median": round(percentile(values, 50.0), 2),
        "p95": round(percentile(values, 95.0), 2),
        "max": round(max(values), 2),
    }


def parse_logs(lines: list[str]) -> dict[str, Any]:
    """Parse log lines and aggregate deduplicated latency entries."""
    session_args: dict[str, Any] = {}
    greeting_entries: dict[str, dict[str, Any]] = {}
    turn_entries: dict[tuple[str, int], dict[str, Any]] = {}
    interrupt_entries: dict[tuple[str, int], dict[str, Any]] = {}

    session_regex = re.compile(
        r"AgentSession args: allow_interruptions=(?P<allow_interruptions>[^,]+), "
        r"min_interruption_duration=(?P<min_interruption_duration>[^,]+), "
        r"min_interruption_words=(?P<min_interruption_words>[^,]+), "
        r"turn_detection=(?P<turn_detection>[^,]+), "
        r"vad=(?P<vad>[^,]+), "
        r"min_endpointing_delay=(?P<min_endpointing_delay>[^,]+), "
        r"max_endpointing_delay=(?P<max_endpointing_delay>[^,]+)"
    )

    for line in lines:
        # 1. Parse AgentSession parameters
        m_sess = session_regex.search(line)
        if m_sess:
            session_args = m_sess.groupdict()

        # 2. Parse Greeting Latency: [LATENCY_GREETING] {json}
        if "[LATENCY_GREETING]" in line:
            raw_json = line[line.index("[LATENCY_GREETING]") + len("[LATENCY_GREETING]") :].strip()
            try:
                data = json.loads(raw_json)
                call_id = data.get("call_id")
                if call_id:
                    greeting_entries[call_id] = data
            except Exception:
                continue

        # 3. Parse Turn Latency: [LATENCY] {json}
        elif "[LATENCY]" in line:
            raw_json = line[line.index("[LATENCY]") + len("[LATENCY]") :].strip()
            try:
                data = json.loads(raw_json)
                call_id = data.get("call_id")
                turn_idx = data.get("turn_idx")
                if call_id is not None and turn_idx is not None:
                    # Exclude greeting turns from conversational turns if turn_idx == 0
                    if turn_idx != 0:
                        turn_entries[(call_id, turn_idx)] = data
            except Exception:
                continue

        # 4. Parse Interruption Latency: [INTERRUPT_LATENCY] {json}
        elif "[INTERRUPT_LATENCY]" in line:
            raw_json = line[line.index("[INTERRUPT_LATENCY]") + len("[INTERRUPT_LATENCY]") :].strip()
            try:
                data = json.loads(raw_json)
                call_id = data.get("call_id")
                turn_idx = data.get("turn_idx")
                if call_id is not None and turn_idx is not None:
                    interrupt_entries[(call_id, turn_idx)] = data
            except Exception:
                continue

    return {
        "session_args": session_args,
        "greetings": list(greeting_entries.values()),
        "turns": list(turn_entries.values()),
        "interruptions": list(interrupt_entries.values()),
    }


def generate_report(parsed: dict[str, Any]) -> str:
    """Generate Markdown tables with min/median/p95/max statistics."""
    lines: list[str] = []

    # 1. AgentSession Configuration
    session_args = parsed.get("session_args") or {}
    lines.append("### AgentSession Configuration")
    if session_args:
        lines.append(f"- allow_interruptions: {session_args.get('allow_interruptions')}")
        lines.append(f"- min_interruption_duration: {session_args.get('min_interruption_duration')}s")
        lines.append(f"- min_interruption_words: {session_args.get('min_interruption_words')}")
        lines.append(f"- turn_detection: {session_args.get('turn_detection')}")
        lines.append(f"- vad: {session_args.get('vad')}")
        lines.append(
            f"- endpointing: min_delay={session_args.get('min_endpointing_delay')}s, "
            f"max_delay={session_args.get('max_endpointing_delay')}s"
        )
    else:
        lines.append("- (No AgentSession configuration found in logs)")
    lines.append("")

    # 2. Conversational Turn Latency
    turns = parsed.get("turns") or []
    lines.append(f"### Conversational Turn Latency (Excluding Greeting, N = {len(turns)})")

    vad_to_stt: list[float] = []
    stt_to_llm: list[float] = []
    llm_to_tts: list[float] = []
    tts_to_publish: list[float] = []
    e2e_total: list[float] = []

    preemptive_count = 0
    interrupted_count = 0

    for turn in turns:
        deltas = turn.get("deltas_ms") or {}
        flags = turn.get("flags") or {}

        if flags.get("preemptive_used"):
            preemptive_count += 1
        if flags.get("interrupted"):
            interrupted_count += 1

        if (v := deltas.get("vad_to_stt")) is not None:
            vad_to_stt.append(float(v))
        if (v := deltas.get("stt_to_llm")) is not None:
            stt_to_llm.append(float(v))
        if (v := deltas.get("llm_to_tts")) is not None:
            llm_to_tts.append(float(v))
        if (v := deltas.get("tts_to_publish")) is not None:
            tts_to_publish.append(float(v))
        if (v := turn.get("total_ms")) is not None:
            e2e_total.append(float(v))

    stages = [
        ("User Stopped -> STT Final", "transcription_delay (derived)", compute_stats(vad_to_stt)),
        ("STT Final -> LLM 1st Token", "llm_node_ttft (duration / raw delta)", compute_stats(stt_to_llm)),
        ("LLM 1st Token -> TTS 1st Audio", "tts_node_ttfb (duration / raw delta)", compute_stats(llm_to_tts)),
        ("TTS 1st Audio -> Audio Published", "playback_latency (derived)", compute_stats(tts_to_publish)),
        ("**Headline: User Stopped -> Audio Published**", "**e2e_latency (headline)**", compute_stats(e2e_total)),
    ]

    lines.append("| Stage | Metric Label | Min (ms) | Median (ms) | P95 (ms) | Max (ms) |")
    lines.append("|---|---|---|---|---|---|")
    for stage_name, label, stats in stages:
        lines.append(
            f"| {stage_name} | {label} | {stats['min']:.1f} | {stats['median']:.1f} | {stats['p95']:.1f} | {stats['max']:.1f} |"
        )
    lines.append("")
    lines.append(f"- preemptive_generation used: {preemptive_count} turns")
    lines.append(f"- interrupted: {interrupted_count} turns")
    lines.append("")

    # 3. Initial Greeting Latency
    greetings = parsed.get("greetings") or []
    lines.append(f"### Initial Greeting Playout (N = {len(greetings)})")
    greeting_delays: list[float] = [
        float(g["join_to_greeting_audio_ms"])
        for g in greetings
        if g.get("join_to_greeting_audio_ms") is not None
    ]
    g_stats = compute_stats(greeting_delays)
    lines.append("| Metric | Min (ms) | Median (ms) | P95 (ms) | Max (ms) |")
    lines.append("|---|---|---|---|---|")
    lines.append(
        f"| Join -> First Greeting Audio | {g_stats['min']:.1f} | {g_stats['median']:.1f} | {g_stats['p95']:.1f} | {g_stats['max']:.1f} |"
    )
    lines.append("")

    # 4. Interruption Latency
    interruptions = parsed.get("interruptions") or []
    lines.append(f"### Interruption Latency (N = {len(interruptions)})")
    onset_to_event: list[float] = []
    event_to_stop: list[float] = []
    total_interrupt: list[float] = []

    for item in interruptions:
        deltas = item.get("deltas_ms") or {}
        if (v := deltas.get("onset_to_event")) is not None:
            onset_to_event.append(float(v))
        if (v := deltas.get("event_to_stop")) is not None:
            event_to_stop.append(float(v))
        if (v := deltas.get("total_interruption_latency")) is not None:
            total_interrupt.append(float(v))

    int_stages = [
        ("User Speech Onset -> Interruption Event", compute_stats(onset_to_event)),
        ("Interruption Event -> Playout Stop", compute_stats(event_to_stop)),
        ("Total Interruption Latency", compute_stats(total_interrupt)),
    ]
    lines.append("| Stage | Min (ms) | Median (ms) | P95 (ms) | Max (ms) |")
    lines.append("|---|---|---|---|---|")
    for name, stats in int_stages:
        lines.append(
            f"| {name} | {stats['min']:.1f} | {stats['median']:.1f} | {stats['p95']:.1f} | {stats['max']:.1f} |"
        )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    lines: list[str] = []
    if len(sys.argv) > 1:
        for file_path in sys.argv[1:]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines.extend(f.readlines())
            except Exception as exc:
                print(f"Error reading file {file_path}: {exc}", file=sys.stderr)
    elif not sys.stdin.isatty():
        lines = sys.stdin.readlines()
    else:
        # Default fallback to agent.log
        try:
            with open("agent.log", "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            print("No log files provided and agent.log not found. Usage: python latency_report.py [log_file ...]", file=sys.stderr)
            sys.exit(1)

    parsed = parse_logs(lines)
    report = generate_report(parsed)
    print(report)


if __name__ == "__main__":
    main()
