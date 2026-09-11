"""Quality Gates: Configurable quality gate evaluation runner for release promotion decisions."""

from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class QualityGateResult(BaseModel):
    gate_name: str
    passed: bool
    score: float
    threshold: float
    message: str


class QualityGateSuiteResult(BaseModel):
    all_passed: bool
    gates: List[QualityGateResult] = Field(default_factory=list)
    failed_gates: List[str] = Field(default_factory=list)


class QualityGateRunner:
    """Evaluates artifacts against configurable quality and assurance gates."""

    def __init__(self):
        self._gates: Dict[str, Callable[[Dict[str, Any]], QualityGateResult]] = {}
        self._register_default_gates()

    def register_gate(self, name: str, gate_fn: Callable[[Dict[str, Any]], QualityGateResult]) -> None:
        self._gates[name] = gate_fn

    def _register_default_gates(self) -> None:
        # 1. Test coverage gate
        def coverage_gate(ctx: Dict[str, Any]) -> QualityGateResult:
            cov = float(ctx.get("coverage_pct", 0.0))
            threshold = float(ctx.get("min_coverage_pct", 80.0))
            passed = cov >= threshold
            msg = f"Coverage {cov:.1f}% meets threshold {threshold:.1f}%" if passed else f"Coverage {cov:.1f}% below required {threshold:.1f}%"
            return QualityGateResult(gate_name="test_coverage", passed=passed, score=cov, threshold=threshold, message=msg)

        # 2. Proof obligations gate
        def proof_count_gate(ctx: Dict[str, Any]) -> QualityGateResult:
            proofs = int(ctx.get("proof_obligations_passed", 0))
            threshold = int(ctx.get("min_proof_obligations", 1))
            passed = proofs >= threshold
            msg = f"{proofs} proof obligations passed" if passed else f"Insufficient proofs: {proofs} < {threshold}"
            return QualityGateResult(gate_name="proof_obligations", passed=passed, score=float(proofs), threshold=float(threshold), message=msg)

        # 3. DSSE attestation presence gate
        def dsse_gate(ctx: Dict[str, Any]) -> QualityGateResult:
            has_envelope = bool(ctx.get("has_dsse_attestation", False))
            passed = has_envelope
            msg = "Valid DSSE attestation present" if passed else "Missing mandatory DSSE attestation"
            return QualityGateResult(gate_name="dsse_attestation", passed=passed, score=1.0 if passed else 0.0, threshold=1.0, message=msg)

        # 4. Minimal Effort Efficiency Score (MEES) gate
        def mees_gate(ctx: Dict[str, Any]) -> QualityGateResult:
            mees = float(ctx.get("mees_score", 100.0))
            threshold = float(ctx.get("min_mees_score", 70.0))
            passed = mees >= threshold
            msg = f"MEES score {mees:.1f} meets threshold {threshold:.1f}" if passed else f"MEES score {mees:.1f} violates complexity threshold"
            return QualityGateResult(gate_name="mees_efficiency", passed=passed, score=mees, threshold=threshold, message=msg)

        self.register_gate("test_coverage", coverage_gate)
        self.register_gate("proof_obligations", proof_count_gate)
        self.register_gate("dsse_attestation", dsse_gate)
        self.register_gate("mees_efficiency", mees_gate)

    def evaluate_all(self, context: Dict[str, Any], gate_names: Optional[List[str]] = None) -> QualityGateSuiteResult:
        targets = gate_names or list(self._gates.keys())
        results = []
        failed = []

        for name in targets:
            gate_fn = self._gates.get(name)
            if not gate_fn:
                continue
            res = gate_fn(context)
            results.append(res)
            if not res.passed:
                failed.append(name)

        return QualityGateSuiteResult(
            all_passed=len(failed) == 0,
            gates=results,
            failed_gates=failed,
        )
