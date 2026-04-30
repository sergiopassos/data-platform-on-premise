from __future__ import annotations

import subprocess
import time

# (service, namespace, local_port, remote_port)
_SERVICES: list[tuple[str, str, int, int]] = [
    ("postgres",                        "infra",          5432,  5432),
    ("minio",                           "infra",          9001,  9001),
    ("kafka-cluster-kafka-bootstrap",   "streaming",      9092,  9092),
    ("kafka-connect",                   "streaming",      8083,  8083),
    ("nessie",                          "infra",         19120, 19120),
    ("airflow-webserver",               "orchestration",  8081,  8080),
    ("trino",                           "serving",        8082,  8080),
    ("langfuse",                        "serving",        3000,  3000),
]

_WARMUP_SECONDS = 3


class PortForwardManager:
    """Start kubectl port-forwards for all platform services and tear them down on exit."""

    def __init__(self) -> None:
        self._procs: list[subprocess.Popen] = []

    def start(self) -> None:
        for svc, ns, lp, rp in _SERVICES:
            proc = subprocess.Popen(
                ["kubectl", "port-forward", f"svc/{svc}", "-n", ns, f"{lp}:{rp}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._procs.append(proc)
            print(f"[port-forward] svc/{svc} ({ns}) {lp}→{rp}  pid={proc.pid}")
        print(f"[port-forward] waiting {_WARMUP_SECONDS}s for tunnels to stabilise…")
        time.sleep(_WARMUP_SECONDS)

    def stop(self) -> None:
        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        self._procs.clear()

    def __enter__(self) -> "PortForwardManager":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
