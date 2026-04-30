from __future__ import annotations

import logging

from agents.state import E2EState

_LOG = logging.getLogger(__name__)


def get_langfuse_trace(trace_id: str) -> dict:
    try:
        from langfuse import Langfuse

        from agents.config import Config
        cfg = Config.from_env()
        if not cfg.langfuse_public_key:
            return {}
        lf = Langfuse(
            host=cfg.langfuse_host,
            public_key=cfg.langfuse_public_key,
            secret_key=cfg.langfuse_secret_key,
        )
        trace = lf.fetch_trace(trace_id)
        return trace.dict() if hasattr(trace, "dict") else vars(trace)
    except Exception as exc:
        _LOG.warning("Could not fetch Langfuse trace: %s", exc)
        return {}


def get_langfuse_scores(trace_id: str) -> list[dict]:
    try:
        from langfuse import Langfuse

        from agents.config import Config
        cfg = Config.from_env()
        if not cfg.langfuse_public_key:
            return []
        lf = Langfuse(
            host=cfg.langfuse_host,
            public_key=cfg.langfuse_public_key,
            secret_key=cfg.langfuse_secret_key,
        )
        scores_page = lf.fetch_scores(trace_id=trace_id)
        raw = scores_page.data if hasattr(scores_page, "data") else []
        return [
            {"name": s.name, "value": s.value, "comment": getattr(s, "comment", "")}
            for s in raw
        ]
    except Exception as exc:
        _LOG.warning("Could not fetch Langfuse scores: %s", exc)
        return []


def format_slack_report(state: E2EState, scores: list[dict]) -> str:
    status_icon = "✅" if state["current_status"] == "SUCCESS" else "❌"
    total_s = sum(state["agent_timings"].values())

    lines = [
        f"{status_icon} *E2E Test Run: {state['run_id']}*",
        f"Status: *{state['current_status']}* | Total: *{total_s:.1f}s*",
        f"Table: `{state['table_name']}` | Gold: `{state['gold_table_fqn']}`",
        "",
        "*Per-Agent Timing:*",
    ]
    for agent, secs in state["agent_timings"].items():
        lines.append(f"  • {agent}: {secs:.1f}s")

    if scores:
        lines += ["", "*Eval Scores:*"]
        for score in scores:
            icon = "✅" if float(score.get("value", 0)) >= 1.0 else "❌"
            lines.append(f"  {icon} {score['name']}: {score['value']}")
    elif state["scores"]:
        lines += ["", "*Eval Scores (from state):*"]
        for name, value in state["scores"].items():
            icon = "✅" if value >= 1.0 else "❌"
            lines.append(f"  {icon} {name}: {value}")

    if state["current_status"] == "ERROR":
        lines += [
            "",
            "*Root-Cause Analysis:*",
            f"  Error: `{state['error_log'] or 'unknown'}`",
            f"  Langfuse Trace ID: `{state['langfuse_trace_id']}`",
            "",
            "*Failed at agent — check agent_timings for last entry*",
        ]

    lines += [
        "",
        f"_Trace: {state['langfuse_trace_id']}_",
    ]
    return "\n".join(lines)
