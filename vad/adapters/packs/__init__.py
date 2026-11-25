"""Built-in Scope B Domain Packs for Infrastructure, Operations, Business, and Compliance."""

from __future__ import annotations

from vad.adapters.domain_pack import DomainKind, DomainPack, DomainPackRegistry
from vad.adapters.packs.business import (
    BusinessPolicyPack,
    BusinessProofExecutor,
    BusinessProposalAdapter,
    create_business_domain_pack,
)
from vad.adapters.packs.compliance import (
    CompliancePolicyPack,
    ComplianceProofExecutor,
    ComplianceProposalAdapter,
    create_compliance_domain_pack,
)
from vad.adapters.packs.infrastructure import (
    InfrastructurePolicyPack,
    InfrastructureProofExecutor,
    InfrastructureProposalAdapter,
    create_infrastructure_domain_pack,
)
from vad.adapters.packs.operations import (
    OperationsPolicyPack,
    OperationsProofExecutor,
    OperationsProposalAdapter,
    create_operations_domain_pack,
)


def create_default_domain_pack_registry() -> DomainPackRegistry:
    """Create a DomainPackRegistry pre-populated with all four built-in Scope B domain packs."""
    registry = DomainPackRegistry()
    registry.register(create_infrastructure_domain_pack())
    registry.register(create_operations_domain_pack())
    registry.register(create_business_domain_pack())
    registry.register(create_compliance_domain_pack())
    return registry


__all__ = [
    "BusinessPolicyPack",
    "BusinessProofExecutor",
    "BusinessProposalAdapter",
    "CompliancePolicyPack",
    "ComplianceProofExecutor",
    "ComplianceProposalAdapter",
    "DomainKind",
    "DomainPack",
    "DomainPackRegistry",
    "InfrastructurePolicyPack",
    "InfrastructureProofExecutor",
    "InfrastructureProposalAdapter",
    "OperationsPolicyPack",
    "OperationsProofExecutor",
    "OperationsProposalAdapter",
    "create_business_domain_pack",
    "create_compliance_domain_pack",
    "create_default_domain_pack_registry",
    "create_infrastructure_domain_pack",
    "create_operations_domain_pack",
]
