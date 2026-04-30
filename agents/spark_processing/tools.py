from __future__ import annotations

import subprocess
import time
from datetime import date
from pathlib import Path

import httpx
import yaml

_BRONZE_MANIFEST_PATH = Path("spark/applications/bronze-streaming-app.yaml")
_SILVER_TEMPLATE_PATH = Path("dags/templates/silver-batch-app.yaml")

_TERMINAL_STATES = {"COMPLETED", "SUCCEEDED", "FAILED"}
_RUNNING_STATES = {"RUNNING"}


def get_sparkapplication_status(name: str, namespace: str = "processing") -> str:
    result = subprocess.run(
        ["kubectl", "get", "sparkapplication", name, "-n", namespace,
         "-o", "jsonpath={.status.applicationState.state}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return "NOT_FOUND"
    return result.stdout.strip() or "UNKNOWN"


def apply_sparkapplication(manifest_yaml: str, namespace: str = "processing") -> str:
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-", "-n", namespace],
        input=manifest_yaml,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl apply failed: {result.stderr.strip()}")
    return result.stdout.strip()


def wait_for_sparkapplication(
    name: str,
    namespace: str,
    expected_states: set[str],
    timeout: int = 600,
    poll_interval: int = 10,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = get_sparkapplication_status(name, namespace)
        if status in expected_states or status == "FAILED":
            return status
        time.sleep(poll_interval)
    raise TimeoutError(f"SparkApplication '{name}' did not reach {expected_states} within {timeout}s")


def get_spark_driver_logs(name: str, namespace: str, tail: int = 100) -> str:
    result = subprocess.run(
        ["kubectl", "logs", "-n", namespace, f"{name}-driver", f"--tail={tail}"],
        capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip() or result.stderr.strip()


def delete_sparkapplication(name: str, namespace: str = "processing") -> None:
    subprocess.run(
        ["kubectl", "delete", "sparkapplication", name, "-n", namespace, "--ignore-not-found"],
        capture_output=True, text=True, timeout=30,
    )


def render_silver_manifest(table_name: str) -> str:
    template = _SILVER_TEMPLATE_PATH.read_text()
    today = date.today().isoformat()
    rendered = (
        template
        .replace("{{ params.table_name }}", table_name)
        .replace("{{ params.date }}", today)
    )
    doc = yaml.safe_load(rendered)
    doc["metadata"]["name"] = f"silver-batch-{table_name}"
    return yaml.dump(doc)


def check_nessie_table_exists(nessie_url: str, namespace: str, table: str) -> bool:
    """Check if {namespace}.{table} exists in Nessie main branch."""
    try:
        resp = httpx.get(f"{nessie_url}/trees/main/entries", timeout=15)
        resp.raise_for_status()
        entries = resp.json().get("entries", [])
        target = f"{namespace}.{table}"
        for entry in entries:
            elements = entry.get("name", {}).get("elements", [])
            if ".".join(elements) == target:
                return True
        return False
    except httpx.HTTPError:
        return False
