from __future__ import annotations

import contextlib
import logging
from typing import Generator

_LOG = logging.getLogger(__name__)

try:
    from langfuse import Langfuse

    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False


def _get_langfuse() -> "Langfuse | None":
    if not _LANGFUSE_AVAILABLE:
        return None
    from agents.config import Config
    cfg = Config.from_env()
    if not cfg.langfuse_public_key:
        return None
    return Langfuse(
        host=cfg.langfuse_host,
        public_key=cfg.langfuse_public_key,
        secret_key=cfg.langfuse_secret_key,
    )


def init_trace(run_id: str) -> str:
    """Create a Langfuse trace for this E2E run. Returns trace_id (== run_id)."""
    lf = _get_langfuse()
    if lf is None:
        _LOG.warning("Langfuse not configured — observability disabled")
        return run_id
    trace = lf.trace(name=f"e2e-run-{run_id}", id=run_id)
    return trace.id


@contextlib.contextmanager
def observe(trace_id: str, name: str) -> Generator[object, None, None]:
    """Wrap a code block in a Langfuse observation span."""
    lf = _get_langfuse()
    if lf is None:
        yield None
        return
    span = lf.span(trace_id=trace_id, name=name)
    try:
        yield span
        span.end()
    except Exception as exc:
        span.end(level="ERROR", status_message=str(exc))
        raise


def emit_score(trace_id: str, name: str, value: float, comment: str = "") -> None:
    lf = _get_langfuse()
    if lf is None:
        return
    lf.score(trace_id=trace_id, name=name, value=value, comment=comment)
