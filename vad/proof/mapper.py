from vad.contracts.models import EIP, ProofKind
from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest


COMMANDS_BY_KIND = {
    ProofKind.UNIT: "pytest",
    ProofKind.PROPERTY: "pytest",
    ProofKind.CONTRACT: "pytest -m contract",
    ProofKind.SECURITY: "pytest -m security",
    ProofKind.PERFORMANCE: "pytest -m performance",
    ProofKind.POLICY: "pytest -m policy",
    ProofKind.RELEASE: "pytest -m release",
    ProofKind.TELEMETRY: "pytest -m telemetry",
}


def map_proofs(eip: EIP) -> ProofPlan:
    _ensure_proof_coverage(eip)
    mappings = []
    manual_gates = []
    for obligation in sorted(eip.proof_obligations, key=lambda item: item.id):
        if obligation.kind == ProofKind.MANUAL_GATE:
            manual_gates.append(obligation.id)
            continue
        command = COMMANDS_BY_KIND.get(obligation.kind)
        if command is None:
            raise ValueError(f"Unsupported proof kind: {obligation.kind}")
        mappings.append(ProofMapping(obligation_id=obligation.id, test_command=command))
    return ProofPlan(
        eip_version=eip.version,
        eip_digest=compute_eip_digest(eip),
        mappings=mappings,
        required_manual_gates=manual_gates,
    )


def _ensure_proof_coverage(eip: EIP) -> None:
    invariant_count = sum(len(values) for values in eip.invariants.model_dump().values())
    success_count = len(eip.goal.success_criteria)
    if (invariant_count or success_count) and not eip.proof_obligations:
        raise ValueError("EIP has success criteria or invariants but no proof obligations.")
