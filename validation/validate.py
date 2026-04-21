"""datacontract-cli wrapper for ODCS v3.1 validation.

Used by the Spark bronze streaming job to validate each record
against the registered contract for its source table.
"""
import json
import subprocess
import tempfile
from pathlib import Path


def validate_record(record: dict, table_name: str, contracts_dir: str = "/contracts") -> tuple[bool, str]:
    contract_path = Path(contracts_dir) / f"{table_name}.yaml"
    if not contract_path.exists():
        return False, f"Contract not found for table '{table_name}' at {contract_path}"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(record, f)
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            [
                "datacontract",
                "test",
                "--contract",
                str(contract_path),
                "--data",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, ""
        error = (result.stderr or result.stdout).strip()
        return False, error
    except subprocess.TimeoutExpired:
        return False, "Validation timeout after 30s"
    except FileNotFoundError:
        return False, "datacontract CLI not found in PATH"
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_batch(records: list[dict], table_name: str, contracts_dir: str = "/contracts") -> list[tuple[bool, str]]:
    return [validate_record(r, table_name, contracts_dir) for r in records]
