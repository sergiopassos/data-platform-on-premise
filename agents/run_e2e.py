"""CLI entrypoint for the E2E agent squad.

Usage:
    python -m agents.run_e2e --table customers
    python -m agents.run_e2e --table customers --gold-table iceberg.gold.customers_orders
    python -m agents.run_e2e --table customers --no-port-forward  # if tunnels are already up
"""
from __future__ import annotations

import argparse
import atexit
import sys

from agents.graph import build_graph
from agents.observability import init_trace
from agents.port_forward import PortForwardManager
from agents.state import initial_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E2E agent squad against the data platform")
    parser.add_argument("--table", required=True, help="Source table name (e.g. customers)")
    parser.add_argument(
        "--gold-table",
        default="iceberg.gold.customers_orders",
        help="Trino-qualified Gold table to validate (default: iceberg.gold.customers_orders)",
    )
    parser.add_argument(
        "--no-port-forward",
        action="store_true",
        help="Skip automatic kubectl port-forward setup (use when tunnels are already active)",
    )
    args = parser.parse_args()

    if not args.no_port_forward:
        pf = PortForwardManager()
        pf.start()
        atexit.register(pf.stop)

    state = initial_state(table_name=args.table, gold_table_fqn=args.gold_table)
    trace_id = init_trace(state["run_id"])
    state = {**state, "langfuse_trace_id": trace_id}

    print(f"[E2E] Starting run {state['run_id']} | table={args.table} | gold={args.gold_table}")

    graph = build_graph()
    final_state = graph.invoke(state)

    report = final_state.get("report_markdown", "")
    if report:
        print("\n" + report)

    status = final_state.get("current_status", "ERROR")
    print(f"\n[E2E] Run {final_state['run_id']} finished with status: {status}")
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
