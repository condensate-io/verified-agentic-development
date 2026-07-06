import shlex
from datetime import datetime
from typing import List
from vad.contracts.models import EIP
from vad.guards.execution import ExecutionGuard
from vad.proof.plan import ProofPlan, VerifyStatus, compute_eip_digest
from vad.verify.report import VerifierReport, VerifierResult

class VerifierRunner:
    def __init__(self, eip: EIP, plan: ProofPlan, execution_guard: ExecutionGuard | None = None, cwd: str | None = None):
        self.eip = eip
        self.plan = plan
        self.execution_guard = execution_guard or ExecutionGuard()
        self.cwd = cwd

    def _check_unmapped(self) -> List[VerifierResult]:
        results = []
        
        for ob in self.eip.proof_obligations:
            if not self.plan.covers_obligation(ob.id):
                results.append(VerifierResult(
                    obligation_id=ob.id,
                    status=VerifyStatus.UNMAPPED,
                    output="",
                    error=f"Obligation {ob.id} is unmapped in ProofPlan."
                ))
        return results

    def run(self) -> VerifierReport:
        results = self._check_plan_matches_eip()
        results.extend(self._check_unmapped())
        passed = len(results) == 0

        # Run mapped tests
        for ob in self.eip.proof_obligations:
            mapping = self.plan.get_mapping(ob.id)
            if mapping:
                cmd = mapping.test_command
                try:
                    result = self.execution_guard.run(shlex.split(cmd), cwd=self.cwd)
                    status = VerifyStatus.PASS if result.allowed and result.exit_code == 0 else VerifyStatus.FAIL
                    passed = passed and (status == VerifyStatus.PASS)
                    results.append(VerifierResult(
                        obligation_id=ob.id,
                        status=status,
                        output=result.stdout,
                        error=result.stderr if status == VerifyStatus.FAIL else None,
                        tool_call={
                            "command": result.command,
                            "cwd": result.cwd,
                            "allowed": result.allowed,
                            "exit_code": result.exit_code,
                            "policy_decision": result.decision.model_dump(mode="json"),
                        },
                    ))
                except Exception as e:
                    passed = False
                    results.append(VerifierResult(
                        obligation_id=ob.id,
                        status=VerifyStatus.FAIL,
                        output="",
                        error=str(e)
                    ))

        return VerifierReport(
            timestamp=datetime.utcnow(),
            results=results,
            passed=passed
        )

    def _check_plan_matches_eip(self) -> List[VerifierResult]:
        errors = []
        if self.plan.eip_version != self.eip.version:
            errors.append("ProofPlan EIP version does not match EIP version.")
        if self.plan.eip_digest != compute_eip_digest(self.eip):
            errors.append("ProofPlan EIP digest does not match EIP content.")
        return [
            VerifierResult(
                obligation_id="proof-plan",
                status=VerifyStatus.FAIL,
                output="",
                error=error,
            )
            for error in errors
        ]
