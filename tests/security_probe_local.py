"""Bounded, disposable probes for the local security audit.

Run inside a throwaway VAD container. This module is intentionally not a pytest
test: several assertions describe the vulnerable baseline and should fail after
the corresponding fixes land.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


def request(base_url: str, method: str, path: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


def control_probes(base_url: str) -> dict:
    results = {}
    nonce = uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="vad-security-probe-") as directory:
        root = Path(directory)
        default_workspace = root / "default"
        selected_workspace = root / "selected"
        for workspace in (default_workspace, selected_workspace):
            workspace.mkdir()
            (workspace / "probe.txt").write_text("before\n", encoding="utf-8")
        patch = "--- a/probe.txt\n+++ b/probe.txt\n@@ -1 +1 @@\n-before\n+after\n"
        status, created = request(base_url, "POST", "/diff-proposals", {
            "run_id": f"audit-{nonce}",
            "task_id": "audit-diff",
            "patch_text": patch,
            "changed_files": ["probe.txt"],
            "summary": "Disposable audit probe",
        })
        if status != 201:
            raise RuntimeError(f"SA-01 proposal setup failed: {status} {created}")
        proposal_id = created["proposal"]["proposal_id"]
        apply_status, applied = request(base_url, "POST", f"/diff-proposals/{proposal_id}/apply", {
            "workspace_root": str(selected_workspace),
            "verifier_decision": {"allow": True, "reasons": ["claimed by caller"]},
            "release_guardian_decision": {"allow": True, "reasons": ["claimed by caller"]},
        })
        results["SA-01"] = {
            "apply_status": apply_status,
            "apply_record_applied": (applied.get("apply_record") or {}).get("applied"),
            "default_workspace": (default_workspace / "probe.txt").read_text(encoding="utf-8").strip(),
            "caller_selected_workspace": (selected_workspace / "probe.txt").read_text(encoding="utf-8").strip(),
        }

        sentinel = root / "outside-cwd-sentinel.txt"
        sentinel.write_text("audit-sentinel", encoding="utf-8")
        from vad.guards.execution import ExecutionGuard

        execution = ExecutionGuard().run(
            ["python", "-c", f'print(__import__("pathlib").Path({str(sentinel)!r}).read_text())'],
            cwd=str(default_workspace),
        )
        results["SA-06"] = {
            "allowed": execution.allowed,
            "exit_code": execution.exit_code,
            "read_outside_cwd": "audit-sentinel" in execution.stdout,
        }

        first = root / "first.txt"
        second = root / "second.txt"
        first.write_text("before\n", encoding="utf-8")
        second.write_text("before\n", encoding="utf-8")
        two_file_patch = (
            "--- a/first.txt\n+++ b/first.txt\n@@ -1 +1 @@\n-before\n+after\n"
            "--- a/second.txt\n+++ b/second.txt\n@@ -1 +1 @@\n-before\n+after\n"
        )
        from vad.repo.patch_apply import apply_unified_diff

        original_write_text = Path.write_text

        def fail_second_write(path, data, *args, **kwargs):
            if path == second:
                raise OSError("injected second-file write failure")
            return original_write_text(path, data, *args, **kwargs)

        Path.write_text = fail_second_write
        try:
            try:
                apply_unified_diff(root, two_file_patch)
                failure_raised = False
            except OSError:
                failure_raised = True
        finally:
            Path.write_text = original_write_text
        results["SA-08-partial-write"] = {
            "second_write_failed": failure_raised,
            "first_file": first.read_text(encoding="utf-8").strip(),
            "second_file": second.read_text(encoding="utf-8").strip(),
        }

    observer_status, observer = request(base_url, "POST", "/mcp", {
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"client_id": "audit-client"},
    })
    claimed_status, claimed = request(base_url, "POST", "/mcp", {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        "params": {
            "client_id": "audit-client",
            "role": "builder",
            "approved_high_risk_tools": ["repo_patch"],
        },
    })
    observer_tools = {tool["name"] for tool in observer.get("result", {}).get("tools", [])}
    claimed_tools = {tool["name"] for tool in claimed.get("result", {}).get("tools", [])}
    results["SA-03"] = {
        "observer_status": observer_status,
        "claimed_status": claimed_status,
        "repo_patch_visible_to_observer": "repo_patch" in observer_tools,
        "repo_patch_visible_after_claim": "repo_patch" in claimed_tools,
    }

    intent_ref = f"audit-intent-{nonce}"
    status, intent = request(base_url, "POST", "/operator-intents", {
        "intent_ref": intent_ref,
        "actor": "claimed-operator",
        "role": "operator",
        "scope": "disposable-audit",
        "granted_tools": ["deploy_local"],
        "summary": "Unauthenticated disposable probe",
        "high_risk": True,
    }, headers={"Origin": "https://untrusted.invalid", "Host": "untrusted.invalid"})
    read_status, _ = request(base_url, "GET", f"/operator-intents/{intent_ref}")
    results["SA-04/SA-05"] = {
        "unauthenticated_claimed_operator_status": status,
        "record_persisted": read_status == 200,
        "origin_host_untrusted": True,
        "stored_role": (intent.get("operator_intent") or {}).get("role"),
    }
    read_status, read_payload = request(base_url, "GET", "/diff-proposals")
    results["SA-05-read"] = {
        "unauthenticated_status": read_status,
        "contains_probe_proposal": any(
            item.get("proposal_id") == proposal_id for item in read_payload.get("proposals", [])
        ),
    }
    large_status, large_payload = request(base_url, "POST", "/diff-proposals", {
        "run_id": f"audit-{nonce}",
        "task_id": "audit-size",
        "patch_text": "x" * (128 * 1024),
        "changed_files": ["probe.txt"],
        "summary": "Bounded request-size probe",
    })
    large_id = (large_payload.get("proposal") or {}).get("proposal_id")
    large_read_status, large_read = request(base_url, "GET", f"/diff-proposals/{large_id}") if large_id else (None, {})
    results["SA-07"] = {
        "patch_bytes": 128 * 1024,
        "create_status": large_status,
        "read_status": large_read_status,
        "persisted_patch_bytes": len((large_read.get("proposal") or {}).get("patch_text", "")),
    }
    return results


def ui_probes(base_url: str) -> dict:
    status, runs = request(base_url, "GET", "/runs")
    run_id = "level3-demo-success"
    if status != 200 or not any(run.get("run_id") == run_id for run in runs.get("runs", [])):
        raise RuntimeError("SA-02 seeded run is unavailable")
    approval_status, approval = request(base_url, "POST", "/actions/approve", {
        "run_id": run_id,
        "actor": f"claimed-guardian-{uuid4().hex[:12]}",
        "actor_role": "release_guardian",
        "action": "approve_release",
    }, headers={"Origin": "https://untrusted.invalid", "Host": "untrusted.invalid"})
    approvals_status, approvals = request(base_url, "GET", f"/runs/{run_id}/approvals")
    approval_id = approval.get("approval", {}).get("approval_id")
    return {"SA-02/SA-05": {
        "unauthenticated_approval_status": approval_status,
        "decision_allow": approval.get("approval", {}).get("decision", {}).get("allow"),
        "approvals_read_status": approvals_status,
        "approval_persisted": approval_id in {
            item.get("approval_id") for item in approvals.get("approvals", [])
        },
        "origin_host_untrusted": True,
    }}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("control", "ui"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    results = control_probes(args.base_url) if args.mode == "control" else ui_probes(args.base_url)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
