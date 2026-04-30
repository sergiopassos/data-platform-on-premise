from __future__ import annotations

import time

from agents.config import Config
from agents.infrastructure.tools import check_minio_bucket, check_namespace_pods
from agents.observability import emit_score, observe
from agents.state import E2EState

_NAMESPACES = ["infra", "streaming", "processing", "orchestration", "serving"]
_BUCKETS = ["warehouse", "bronze", "contracts"]
_TERMINAL_PHASES = {"Succeeded", "Completed"}


def _pod_not_ready(pod: dict) -> bool:
    phase = pod.get("phase", "Unknown")
    if phase in _TERMINAL_PHASES:
        return False
    ready = pod.get("ready")
    if ready is not None:
        return ready != "True"
    return phase != "Running"


def infra_node(state: E2EState) -> E2EState:
    start = time.monotonic()
    cfg = Config.from_env()

    with observe(state["langfuse_trace_id"], name="infra_health_check"):
        try:
            for ns in _NAMESPACES:
                pods = check_namespace_pods(ns)
                not_ready = [p for p in pods if _pod_not_ready(p)]
                if not_ready:
                    names = [p["name"] for p in not_ready]
                    raise RuntimeError(f"Namespace '{ns}': pods not ready: {names}")

            for bucket in _BUCKETS:
                if not check_minio_bucket(bucket, cfg):
                    raise RuntimeError(f"MinIO bucket missing: '{bucket}'")

            emit_score(state["langfuse_trace_id"], "infra_health", 1.0)
            elapsed = time.monotonic() - start
            return {
                **state,
                "last_completed": "infra",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "infra": elapsed},
                "scores": {**state["scores"], "infra_health": 1.0},
            }

        except Exception as exc:
            emit_score(state["langfuse_trace_id"], "infra_health", 0.0, comment=str(exc))
            elapsed = time.monotonic() - start
            return {
                **state,
                "current_status": "ERROR",
                "error_log": f"[Infrastructure] {exc}",
                "last_completed": "infra",
                "next_agent": "orchestrator",
                "agent_timings": {**state["agent_timings"], "infra": elapsed},
                "scores": {**state["scores"], "infra_health": 0.0},
            }
