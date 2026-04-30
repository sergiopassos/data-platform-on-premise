from __future__ import annotations

import json
import subprocess

import boto3
from botocore.exceptions import ClientError

from agents.config import Config


def check_namespace_pods(namespace: str) -> list[dict[str, str]]:
    """Return list of {name, ready} for all pods in namespace using JSON for reliable parsing."""
    result = subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kubectl get pods -n {namespace}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    pods = []
    for item in data.get("items", []):
        name = item["metadata"]["name"]
        phase = item.get("status", {}).get("phase", "Unknown")
        conditions = item.get("status", {}).get("conditions", [])
        ready = next((c["status"] for c in conditions if c["type"] == "Ready"), None)
        pods.append({"name": name, "phase": phase, "ready": ready})
    return pods


def check_minio_bucket(bucket: str, cfg: Config) -> bool:
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.minio_endpoint,
        aws_access_key_id=cfg.minio_access_key,
        aws_secret_access_key=cfg.minio_secret_key,
    )
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False
