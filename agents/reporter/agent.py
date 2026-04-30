from __future__ import annotations

import time

from agents.observability import observe
from agents.reporter.tools import format_slack_report, get_langfuse_scores
from agents.state import E2EState


def reporter_node(state: E2EState) -> E2EState:
    start = time.monotonic()

    with observe(state["langfuse_trace_id"], name="reporter_audit"):
        scores = get_langfuse_scores(state["langfuse_trace_id"])
        report = format_slack_report(state, scores)
        elapsed = time.monotonic() - start
        return {
            **state,
            "last_completed": "reporter",
            "next_agent": "reporter",
            "report_markdown": report,
            "agent_timings": {**state["agent_timings"], "reporter": elapsed},
        }
