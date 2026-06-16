import subprocess
from datetime import datetime
from typing import List
from vad.contracts.models import EIP
from vad.proof.plan import ProofPlan, VerifyStatus
from vad.verify.report import VerifierReport, VerifierResult

class VerifierRunner:
    def __init__(self, eip: EIP, plan: ProofPlan):
        self.eip = eip
        self.plan = plan

    def _check_unmapped(self) -> List[VerifierResult]:
        results = []
        mapped_ids = {m.obligation_id for m in self.plan.mappings}
        
        for ob in self.eip.proof_obligations:
            if ob.id not in mapped_ids:
                results.append(VerifierResult(
                    obligation_id=ob.id,
                    status=VerifyStatus.UNMAPPED,
                    output="",
                    error=f"Obligation {ob.id} is unmapped in ProofPlan."
                ))
        return results

    def run(self) -> VerifierReport:
        results = self._check_unmapped()
        passed = len(results) == 0

        # Run mapped tests
        for ob in self.eip.proof_obligations:
            mapping = self.plan.get_mapping(ob.id)
            if mapping:
                cmd = mapping.test_command
                try:
                    proc = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True
                    )
                    status = VerifyStatus.PASS if proc.returncode == 0 else VerifyStatus.FAIL
                    passed = passed and (status == VerifyStatus.PASS)
                    results.append(VerifierResult(
                        obligation_id=ob.id,
                        status=status,
                        output=proc.stdout,
                        error=proc.stderr if proc.returncode != 0 else None
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
