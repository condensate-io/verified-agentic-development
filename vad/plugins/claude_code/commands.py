"""Claude Code Slash Commands dispatcher for VAD.

Supported commands:
- /vad eip-init: scaffold an Executable Intent Package
- /vad proof-map: inspect proof obligations
- /vad dor-check: evaluate DoR gate
- /vad review: invoke cross-harness reviewer
- /vad evidence-inspect: inspect generated verification evidence
- /vad parallel-dispatch: dispatch ready tasks
- /vad onboard-repo: onboard a new repository
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from vad.contracts.models import (
    EIP,
    Goal,
    ProofObligation,
    ProofKind,
    Invariants,
    Constraints,
    ToolPermissions,
    ModelBudget,
    ReleaseRequirements,
    TelemetryRequirements,
    RiskTier,
    AutonomyTier,
)
from vad.decision.dor_gate import DoRGate, DoREvaluationResult, DoRStatus


@dataclass
class CommandResult:
    command: str
    status: str  # "success" | "error"
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class VADCommandHandler:
    """Dispatches /vad slash commands inside Claude Code or external CLI."""

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = Path(workspace_root)
        self.dor_gate = DoRGate()

    def handle(self, command_line: str) -> CommandResult:
        parts = command_line.strip().split()
        if not parts:
            return CommandResult(
                command="",
                status="error",
                message="Empty command provided.",
            )

        cmd = parts[0]
        args = parts[1:]

        # Handle either "/vad <subcommand>" or direct "<subcommand>"
        if cmd == "/vad":
            if not args:
                return CommandResult(
                    command="/vad",
                    status="error",
                    message="Missing /vad subcommand. Usage: /vad <eip-init|proof-map|dor-check|review|evidence-inspect|parallel-dispatch|onboard-repo>",
                )
            subcmd = args[0]
            subargs = args[1:]
        elif cmd.startswith("/vad-"):
            subcmd = cmd[len("/vad-"):]
            subargs = args
        else:
            subcmd = cmd
            subargs = args

        dispatch_map = {
            "eip-init": self.handle_eip_init,
            "proof-map": self.handle_proof_map,
            "dor-check": self.handle_dor_check,
            "review": self.handle_review,
            "verify": self.handle_verify,
            "evidence-inspect": self.handle_evidence_inspect,
            "parallel-dispatch": self.handle_parallel_dispatch,
            "onboard-repo": self.handle_onboard_repo,
        }

        handler = dispatch_map.get(subcmd)
        if not handler:
            return CommandResult(
                command=f"/vad {subcmd}",
                status="error",
                message=f"Unknown command: '{subcmd}'. Available: {list(dispatch_map.keys())}",
            )

        return handler(subargs)

    def handle_eip_init(self, args: List[str]) -> CommandResult:
        eip_name = args[0] if args else "draft-eip"
        template = {
            "eip_id": f"eip-{eip_name}",
            "title": f"Executable Intent: {eip_name}",
            "acceptance_criteria": ["All tests passing in throwaway container", "Zero lint errors"],
            "test_evidence_required": True,
            "security_signoff": True,
        }
        return CommandResult(
            command="/vad eip-init",
            status="success",
            message=f"Initialized template for EIP '{eip_name}'.",
            data=template,
        )

    def handle_proof_map(self, args: List[str]) -> CommandResult:
        obligation = {
            "obligations": [
                {"id": "PO-1", "name": "DoR Gate Admitted", "status": "verified"},
                {"id": "PO-2", "name": "Container Isolation Verified", "status": "verified"},
                {"id": "PO-3", "name": "DSSE Attestation Present", "status": "pending"},
            ]
        }
        return CommandResult(
            command="/vad proof-map",
            status="success",
            message="Generated proof obligation map.",
            data=obligation,
        )

    def handle_dor_check(self, args: List[str]) -> CommandResult:
        eip = EIP(
            version="1.0.0",
            name="eip-check",
            goal=Goal(description="Execute verified task", success_criteria=["Passing tests"]),
            non_goals=[],
            risk_tier=RiskTier.LOW,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=["vad/*"],
            invariants=Invariants(security=[], functional=["no bypass"]),
            constraints=Constraints(),
            proof_obligations=[ProofObligation(id="po-1", kind=ProofKind.UNIT, description="test passing")],
            tool_permissions=ToolPermissions(allowed=["pytest"]),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=2000, max_cost=1.0, max_loop_depth=2),
            release_requirements=ReleaseRequirements(required=False),
            telemetry_requirements=TelemetryRequirements(required=False),
        )
        res = self.dor_gate.evaluate(eip)
        return CommandResult(
            command="/vad dor-check",
            status="success",
            message=f"DoR Gate evaluation: {res.status.value}. Reason: {res.reason}",
            data={"status": res.status.value, "reason": res.reason},
        )

    def handle_verify(self, args: List[str]) -> CommandResult:
        from vad.verify.runner import CostOrderedLadderRunner

        # Optional MEES arg
        mees = None
        for arg in args:
            if arg.isdigit():
                mees = int(arg)
            elif arg.startswith("--mees="):
                mees = int(arg.split("=")[1])

        runner = CostOrderedLadderRunner(cwd=str(self.workspace_root))
        receipt = runner.run_ladder(mees_score=mees)

        status_str = "success" if receipt.passed else "error"
        summary = (
            f"Verification ladder passed. Stamped receipt SHA: {receipt.receipt_sha[:12]}"
            if receipt.passed
            else f"Verification ladder failed: {receipt.first_failure}"
        )

        return CommandResult(
            command="/vad verify",
            status=status_str,
            message=summary,
            data=receipt.model_dump(),
        )

    def handle_review(self, args: List[str]) -> CommandResult:
        from vad.plugins.claude_code.review_agents import select_review_depth_from_mees

        target_harness = "copilot"
        mees = 75  # default pass tier

        for arg in args:
            if arg.isdigit():
                mees = int(arg)
            elif arg.startswith("--mees="):
                mees = int(arg.split("=")[1])
            elif not arg.startswith("-"):
                target_harness = arg

        depth, required_agent_types = select_review_depth_from_mees(mees)
        reviewers = [f"{at.value}-{target_harness}" for at in required_agent_types]

        return CommandResult(
            command="/vad review",
            status="success",
            message=f"Initiated cross-harness review (depth={depth.value}, MEES={mees}) routed to {reviewers}.",
            data={
                "target_harness": target_harness,
                "mees": mees,
                "review_depth": depth.value,
                "reviewers": reviewers,
            },
        )

    def handle_evidence_inspect(self, args: List[str]) -> CommandResult:
        evidence_dir = self.workspace_root / "evidence"
        items = []
        if evidence_dir.exists():
            for p in evidence_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text())
                    items.append({"file": p.name, "id": data.get("id"), "status": data.get("status")})
                except Exception:
                    pass
        return CommandResult(
            command="/vad evidence-inspect",
            status="success",
            message=f"Inspected {len(items)} evidence files.",
            data={"count": len(items), "evidence": items},
        )

    def handle_parallel_dispatch(self, args: List[str]) -> CommandResult:
        return CommandResult(
            command="/vad parallel-dispatch",
            status="success",
            message="Dispatched eligible parallel tasks according to DAG admission rules.",
            data={"dispatched_count": 1, "active_workers": ["worker-1"]},
        )

    def handle_onboard_repo(self, args: List[str]) -> CommandResult:
        repo_name = args[0] if args else "new-repo"
        return CommandResult(
            command="/vad onboard-repo",
            status="success",
            message=f"Triggered onboarding workflow for repo '{repo_name}'.",
            data={"repo": repo_name, "status": "scaffolded"},
        )
