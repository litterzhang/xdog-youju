"""Public operator script stays dry-run unless the caller explicitly opts in."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_model_audit.py"


def test_dry_run_checks_frozen_batch_without_provider_or_ledger(tmp_path):
    path, ledger = tmp_path / "cases.json", tmp_path / "audit.sqlite3"
    case = {
        "case_id": "fixture",
        "documents": [{"document_id": "v1", "text": "Original text"}],
        "current_output": {"validity": {"status": "unknown"}},
        "output_status": "review",
        "issues": [{"code": "UNRESOLVED_PERIOD"}],
    }
    path.write_text(json.dumps([case]))
    command = [sys.executable, str(SCRIPT), "--input", str(path), "--ledger", str(ledger)]
    result = subprocess.run(command, text=True, capture_output=True, check=True, timeout=20)
    output = json.loads(result.stdout)
    assert output["status"] == "dry_run" and output["maximum_requests"] == 1
    assert output["provider_calls"] == 0 and output["model"] == "gpt-6.1-sol"
    assert not ledger.exists()
    path.write_text(json.dumps([case, dict(case, case_id="second")]))
    refused = subprocess.run(command, text=True, capture_output=True, timeout=20)
    assert refused.returncode != 0 and "max_cases" in refused.stderr
    assert not ledger.exists()
