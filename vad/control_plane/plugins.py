from __future__ import annotations

import hashlib
import json
import shutil
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus
from vad.control_plane.work_items import WorkItem

from vad.signing.local import LocalDevelopmentSigner, payload_digest
from vad.signing.models import SignatureEnvelope


SafeIdentifier = Annotated[str, Field(min_length=1, max_length=160)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class PluginTargetClient(str, Enum):
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    VS_CODE = "vscode"
    ANTIGRAVITY = "antigravity"
    WINDSURF = "windsurf"
    CURSOR = "cursor"
    OPENCODE = "opencode"
    GENERIC_MCP = "generic_mcp"


class PluginConfigScope(str, Enum):
    USER = "user"
    WORKSPACE = "workspace"
    PROJECT = "project"


class PluginCommand(BaseModel):
    executable: str = Field(min_length=1, max_length=240)
    args: tuple[str, ...] = ()
    env: dict[str, str] = {}

    @field_validator("executable", "args")
    @classmethod
    def command_parts_must_be_safe(cls, value):
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            _validate_text(item, "command parts")
        return value

    @field_validator("env")
    @classmethod
    def env_values_must_not_embed_secrets(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for key, value in values.items():
            _validate_identifier(key, "environment keys")
            _validate_text(value, "environment values")
            if _looks_secret(key) or _looks_secret(value):
                raise ValueError("plugin commands must not embed secret environment values")
            normalized[key] = value
        return dict(sorted(normalized.items()))


class PluginConfigPath(BaseModel):
    scope: PluginConfigScope
    path: str = Field(min_length=1, max_length=300)
    required: bool = True

    @field_validator("path")
    @classmethod
    def path_must_be_reviewable(cls, value: str) -> str:
        _validate_text(value, "config paths")
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("plugin config paths must be relative and reviewable")
        if _looks_secret(value):
            raise ValueError("plugin config paths must not contain secret markers")
        return value


class PluginPermission(BaseModel):
    name: SafeIdentifier
    reason: str = Field(min_length=1, max_length=300)
    high_risk: bool = False

    @field_validator("name")
    @classmethod
    def name_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "permission names")

    @field_validator("reason")
    @classmethod
    def reason_must_be_safe(cls, value: str) -> str:
        _validate_text(value, "permission reasons")
        return value


class PluginToolGrant(BaseModel):
    name: SafeIdentifier
    role: SafeIdentifier
    high_risk: bool = False
    approved_by_default: bool = False

    @field_validator("name", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "tool grant identifiers")

    @field_validator("approved_by_default")
    @classmethod
    def high_risk_tools_are_never_auto_approved(cls, value: bool, info):
        if value and info.data.get("high_risk"):
            raise ValueError("high-risk plugin tools must not be approved by default")
        return value


class PluginPrompt(BaseModel):
    prompt_id: SafeIdentifier
    role: SafeIdentifier
    path: str = Field(min_length=1, max_length=300)

    @field_validator("prompt_id", "role")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "prompt identifiers")

    @field_validator("path")
    @classmethod
    def prompt_path_must_be_relative(cls, value: str) -> str:
        _validate_text(value, "prompt paths")
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("plugin prompt paths must be relative")
        return value


class VADPluginManifest(BaseModel):
    plugin_id: SafeIdentifier
    target_client: PluginTargetClient
    version: str = Field(pattern=r"^\d+\.\d+\.\d+([-.][A-Za-z0-9.]+)?$")
    command: PluginCommand
    config_paths: tuple[PluginConfigPath, ...] = Field(min_length=1)
    permissions: tuple[PluginPermission, ...] = ()
    tools: tuple[PluginToolGrant, ...] = ()
    prompts: tuple[PluginPrompt, ...] = ()
    checksums: dict[str, Digest] = Field(min_length=1)

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")

    @field_validator("permissions", "tools", "prompts")
    @classmethod
    def repeated_items_must_be_unique(cls, values: tuple[BaseModel, ...]) -> tuple[BaseModel, ...]:
        seen = set()
        for item in values:
            item_id = getattr(item, "name", None) or getattr(item, "prompt_id", None)
            if item_id in seen:
                raise ValueError("plugin manifest entries must be unique")
            seen.add(item_id)
        return values

    @field_validator("checksums")
    @classmethod
    def checksum_paths_must_be_safe(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for path, digest in values.items():
            _validate_text(path, "checksum paths")
            if path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
                raise ValueError("plugin checksum paths must be relative")
            normalized[path] = digest
        return dict(sorted(normalized.items()))

    @classmethod
    def json_schema(cls) -> dict:
        return cls.model_json_schema()


class PluginArtifactHash(BaseModel):
    plugin_id: SafeIdentifier
    version: str = Field(pattern=r"^\d+\.\d+\.\d+([-.][A-Za-z0-9.]+)?$")
    manifest_digest: Digest
    artifact_digest: Digest
    file_digests: dict[str, Digest] = Field(min_length=1)

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")

    @field_validator("file_digests")
    @classmethod
    def file_digest_paths_must_be_safe(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for path, digest in values.items():
            _validate_relative_path(path, "artifact file paths")
            normalized[path] = digest
        return dict(sorted(normalized.items()))


class PluginArtifactSignature(BaseModel):
    signature: SignatureEnvelope


class PluginArtifactVerification(BaseModel):
    plugin_id: SafeIdentifier
    version: str
    artifact_digest: Digest
    manifest_digest: Digest
    file_count: int = Field(ge=1)
    signature_present: bool = False
    signature_verified: bool = False
    signer_key_id: str | None = None
    evidence: str = Field(min_length=1, max_length=300)

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")


class PluginStatus(str, Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class PluginStatusRecord(BaseModel):
    plugin_id: SafeIdentifier
    target_client: PluginTargetClient
    status: PluginStatus
    version: str
    local_version: str | None = Field(default=None, max_length=80)
    publication_readiness: str = Field(default="needs_review", max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    action_required: str | None = Field(default=None, max_length=300)

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")

    @field_validator("local_version", "publication_readiness", "summary", "action_required")
    @classmethod
    def text_must_be_safe(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_text(value, "plugin status text")
        return value


class PluginInventoryReviewState(str, Enum):
    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    BLOCKED = "blocked"


class PluginInventoryRecord(BaseModel):
    plugin_id: SafeIdentifier
    target_client: PluginTargetClient
    version: str
    review_state: PluginInventoryReviewState = PluginInventoryReviewState.PENDING_REVIEW
    applied_config_hashes: dict[str, Digest] = Field(default_factory=dict)
    backup_paths: tuple[str, ...] = ()
    uninstall_status: str = Field(default="not_requested", max_length=80)
    rollback_status: str = Field(default="not_requested", max_length=80)
    dashboard_status: PluginStatus = PluginStatus.NEEDS_REVIEW
    publication_readiness: str = Field(default="needs_operator_review", max_length=80)
    summary: str = Field(min_length=1, max_length=300)
    action_required: str | None = Field(default=None, max_length=300)
    artifact_digest: Digest | None = None
    manifest_digest: Digest | None = None

    @field_validator("plugin_id")
    @classmethod
    def inventory_plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")

    @field_validator("applied_config_hashes")
    @classmethod
    def config_hash_paths_must_be_safe(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for path, digest in values.items():
            _validate_relative_path(path, "applied config hash paths")
            normalized[path] = digest
        return dict(sorted(normalized.items()))

    @field_validator(
        "backup_paths",
        "uninstall_status",
        "rollback_status",
        "publication_readiness",
        "summary",
        "action_required",
    )
    @classmethod
    def inventory_text_must_be_safe(cls, value):
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            if item is not None:
                _validate_text(str(item), "plugin inventory text")
        return value

    def to_status_record(self) -> PluginStatusRecord:
        return PluginStatusRecord(
            plugin_id=self.plugin_id,
            target_client=self.target_client,
            status=self.dashboard_status,
            version=self.version,
            local_version=self.version if self.dashboard_status == PluginStatus.INSTALLED else None,
            publication_readiness=self.publication_readiness,
            summary=self.summary,
            action_required=self.action_required,
        )


class PluginInstallAction(str, Enum):
    WRITE_CONFIG = "write_config"


class PluginInstallOperation(BaseModel):
    action: PluginInstallAction
    scope: PluginConfigScope
    path: str = Field(min_length=1, max_length=500)
    change: str = Field(min_length=1, max_length=300)
    content: dict


class PluginRollbackOperation(BaseModel):
    action: str = Field(min_length=1, max_length=80)
    path: str = Field(min_length=1, max_length=500)
    backup_path: str | None = None


class PluginInstallerDryRun(BaseModel):
    plugin_id: SafeIdentifier
    target_client: PluginTargetClient
    version: str
    dry_run: bool = True
    writes_performed: int = 0
    operations: tuple[PluginInstallOperation, ...]
    rollback: tuple[PluginRollbackOperation, ...]
    artifact: PluginArtifactVerification

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")


class PluginOperationResult(BaseModel):
    plugin_id: SafeIdentifier
    target_client: PluginTargetClient
    version: str
    operation: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=80)
    writes_performed: int = Field(ge=0)
    applied_config_hashes: dict[str, Digest] = Field(default_factory=dict)
    backup_paths: tuple[str, ...] = ()
    inventory: PluginInventoryRecord
    evidence: str = Field(min_length=1, max_length=300)

    @field_validator("plugin_id")
    @classmethod
    def operation_plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")

    @field_validator("operation", "status", "backup_paths", "evidence")
    @classmethod
    def operation_text_must_be_safe(cls, value):
        values = value if isinstance(value, tuple) else (value,)
        for item in values:
            _validate_text(str(item), "plugin operation text")
        return value

    @field_validator("applied_config_hashes")
    @classmethod
    def operation_config_hash_paths_must_be_safe(cls, values: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for path, digest in values.items():
            _validate_relative_path(path, "plugin operation config hash paths")
            normalized[path] = digest
        return dict(sorted(normalized.items()))


class PluginSecurityAuditFinding(BaseModel):
    check: SafeIdentifier
    detail: str = Field(min_length=1, max_length=300)

    @field_validator("check")
    @classmethod
    def check_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "security audit checks")

    @field_validator("detail")
    @classmethod
    def detail_must_be_safe(cls, value: str) -> str:
        _validate_text(value, "security audit findings")
        return value


class PluginSecurityAuditResult(BaseModel):
    plugin_id: SafeIdentifier
    passed: bool
    findings: tuple[PluginSecurityAuditFinding, ...] = ()

    @field_validator("plugin_id")
    @classmethod
    def plugin_id_must_be_safe(cls, value: str) -> str:
        return _validate_identifier(value, "plugin_id")


def compute_plugin_artifact_hash(manifest: VADPluginManifest) -> PluginArtifactHash:
    manifest_payload = manifest.model_dump(mode="json")
    manifest_digest = payload_digest(manifest_payload)
    file_digests = dict(sorted(manifest.checksums.items()))
    artifact_payload = {
        "plugin_id": manifest.plugin_id,
        "version": manifest.version,
        "manifest_digest": manifest_digest,
        "file_digests": file_digests,
    }
    return PluginArtifactHash(
        plugin_id=manifest.plugin_id,
        version=manifest.version,
        manifest_digest=manifest_digest,
        artifact_digest=payload_digest(artifact_payload),
        file_digests=file_digests,
    )


def create_plugin_installer_dry_run(
    manifest: VADPluginManifest,
    *,
    workspace_root: Path,
    user_config_root: Path,
    signature: PluginArtifactSignature | None = None,
    signer: LocalDevelopmentSigner | None = None,
) -> PluginInstallerDryRun:
    workspace_root = workspace_root.resolve()
    user_config_root = user_config_root.resolve()
    operations = []
    rollback = []
    artifact = verify_plugin_artifact(manifest, signature=signature, signer=signer)
    for config_path in manifest.config_paths:
        if config_path.scope == PluginConfigScope.PROJECT:
            raise ValueError("installer dry-run writes are limited to user and workspace config paths")
        root = user_config_root if config_path.scope == PluginConfigScope.USER else workspace_root
        target = _resolve_under(root, config_path.path, "installer config path")
        content = _plugin_config_content(manifest, artifact)
        operations.append(PluginInstallOperation(
            action=PluginInstallAction.WRITE_CONFIG,
            scope=config_path.scope,
            path=str(target),
            change=f"write {manifest.plugin_id} config for {manifest.target_client.value}",
            content=content,
        ))
        rollback.append(PluginRollbackOperation(
            action="restore_or_remove",
            path=str(target),
            backup_path=f"{target}.vad-backup",
        ))
    return PluginInstallerDryRun(
        plugin_id=manifest.plugin_id,
        target_client=manifest.target_client,
        version=manifest.version,
        operations=tuple(operations),
        rollback=tuple(rollback),
        artifact=artifact,
    )


def audit_plugin_artifact_security(
    manifest: VADPluginManifest,
    dry_run: PluginInstallerDryRun,
    *,
    workspace_root: Path,
    user_config_root: Path,
) -> PluginSecurityAuditResult:
    findings: list[PluginSecurityAuditFinding] = []
    workspace_root = workspace_root.resolve()
    user_config_root = user_config_root.resolve()

    if dry_run.plugin_id != manifest.plugin_id:
        findings.append(_audit_finding("artifact_binding", "dry-run plugin id does not match manifest"))
    if not dry_run.dry_run or dry_run.writes_performed != 0:
        findings.append(_audit_finding("dry_run_only", "plugin audit requires a no-write dry-run plan"))

    for operation in dry_run.operations:
        if operation.action != PluginInstallAction.WRITE_CONFIG:
            findings.append(_audit_finding("guarded_write", f"unsupported install action {operation.action.value}"))
        if operation.scope == PluginConfigScope.PROJECT:
            findings.append(_audit_finding("guarded_write", f"project-scoped write is not allowed for {operation.path}"))
        root = user_config_root if operation.scope == PluginConfigScope.USER else workspace_root
        if not _path_is_under(Path(operation.path), root):
            findings.append(_audit_finding("guarded_write", f"operation path escapes approved {operation.scope.value} root"))
        for value in _walk_values(operation.content):
            if _looks_secret(str(value)):
                findings.append(_audit_finding("no_secrets", "generated config contains a secret marker"))
            if _has_non_local_http_endpoint(str(value)):
                findings.append(_audit_finding("no_cloud_default", "generated config contains a non-local HTTP endpoint"))

    for tool in manifest.tools:
        if tool.high_risk and tool.approved_by_default:
            findings.append(_audit_finding("dangerous_auto_approval", f"high-risk tool {tool.name} is approved by default"))

    for value in _manifest_scan_values(manifest):
        text = str(value)
        if _looks_secret(text):
            findings.append(_audit_finding("no_secrets", "plugin manifest contains a secret marker"))
        if _has_non_local_http_endpoint(text):
            findings.append(_audit_finding("no_cloud_default", "plugin manifest contains a non-local HTTP endpoint"))

    return PluginSecurityAuditResult(
        plugin_id=manifest.plugin_id,
        passed=not findings,
        findings=tuple(findings),
    )


def apply_plugin_installer_plan(
    dry_run: PluginInstallerDryRun,
    *,
    workspace_root: Path,
    user_config_root: Path,
    approval_ref: str,
) -> PluginOperationResult:
    _validate_approval_ref(approval_ref)
    roots = _resolved_roots(workspace_root, user_config_root)
    _validate_apply_plan(dry_run, roots)
    applied_hashes: dict[str, str] = {}
    backup_paths: list[str] = []
    writes = 0

    for operation, rollback in zip(dry_run.operations, dry_run.rollback):
        target = _guarded_operation_path(operation, roots)
        backup = _guarded_backup_path(rollback, operation, roots)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backup_paths.append(_inventory_path(backup, operation.scope, roots))
        payload = _canonical_json_bytes(operation.content)
        target.write_bytes(payload)
        applied_hashes[_inventory_path(target, operation.scope, roots)] = _sha256_bytes(payload)
        writes += 1

    inventory = PluginInventoryRecord(
        plugin_id=dry_run.plugin_id,
        target_client=dry_run.target_client,
        version=dry_run.version,
        review_state=PluginInventoryReviewState.APPROVED,
        applied_config_hashes=applied_hashes,
        backup_paths=tuple(backup_paths),
        uninstall_status="available",
        rollback_status="ready",
        dashboard_status=PluginStatus.INSTALLED,
        publication_readiness="local_ready",
        summary=f"{dry_run.plugin_id} applied to local {dry_run.target_client.value} configuration.",
        action_required=None,
        artifact_digest=dry_run.artifact.artifact_digest,
        manifest_digest=dry_run.artifact.manifest_digest,
    )
    return PluginOperationResult(
        plugin_id=dry_run.plugin_id,
        target_client=dry_run.target_client,
        version=dry_run.version,
        operation="apply",
        status="applied",
        writes_performed=writes,
        applied_config_hashes=applied_hashes,
        backup_paths=tuple(backup_paths),
        inventory=inventory,
        evidence=f"Applied reviewed plugin dry-run with approval {approval_ref}.",
    )


def uninstall_plugin_installation(
    dry_run: PluginInstallerDryRun,
    inventory: PluginInventoryRecord,
    *,
    workspace_root: Path,
    user_config_root: Path,
    approval_ref: str,
) -> PluginOperationResult:
    _validate_approval_ref(approval_ref)
    roots = _resolved_roots(workspace_root, user_config_root)
    _validate_inventory_matches_plan(dry_run, inventory)
    writes = 0
    for operation in dry_run.operations:
        target = _guarded_operation_path(operation, roots)
        key = _inventory_path(target, operation.scope, roots)
        expected_digest = inventory.applied_config_hashes.get(key)
        if expected_digest is None:
            raise ValueError(f"inventory is missing applied hash for {key}")
        if target.exists():
            actual_digest = _sha256_bytes(target.read_bytes())
            if actual_digest != expected_digest:
                raise ValueError(f"installed plugin config drifted at {key}")
            target.unlink()
            writes += 1

    updated = inventory.model_copy(update={
        "applied_config_hashes": {},
        "uninstall_status": "uninstalled",
        "rollback_status": inventory.rollback_status,
        "dashboard_status": PluginStatus.AVAILABLE,
        "publication_readiness": "dry_run_ready",
        "summary": f"{dry_run.plugin_id} uninstalled from local {dry_run.target_client.value} configuration.",
        "action_required": "Review dry-run output before applying again.",
    })
    return PluginOperationResult(
        plugin_id=dry_run.plugin_id,
        target_client=dry_run.target_client,
        version=dry_run.version,
        operation="uninstall",
        status="uninstalled",
        writes_performed=writes,
        applied_config_hashes={},
        backup_paths=inventory.backup_paths,
        inventory=updated,
        evidence=f"Removed applied plugin config with approval {approval_ref}.",
    )


def rollback_plugin_installation(
    dry_run: PluginInstallerDryRun,
    inventory: PluginInventoryRecord,
    *,
    workspace_root: Path,
    user_config_root: Path,
    approval_ref: str,
) -> PluginOperationResult:
    _validate_approval_ref(approval_ref)
    roots = _resolved_roots(workspace_root, user_config_root)
    _validate_inventory_matches_plan(dry_run, inventory)
    writes = 0
    for operation, rollback in zip(dry_run.operations, dry_run.rollback):
        target = _guarded_operation_path(operation, roots)
        backup = _guarded_backup_path(rollback, operation, roots)
        key = _inventory_path(target, operation.scope, roots)
        expected_digest = inventory.applied_config_hashes.get(key)
        if expected_digest is None:
            raise ValueError(f"inventory is missing applied hash for {key}")
        if target.exists() and _sha256_bytes(target.read_bytes()) != expected_digest:
            raise ValueError(f"installed plugin config drifted at {key}")
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        elif target.exists():
            target.unlink()
        writes += 1

    updated = inventory.model_copy(update={
        "applied_config_hashes": {},
        "rollback_status": "rolled_back",
        "uninstall_status": inventory.uninstall_status,
        "dashboard_status": PluginStatus.AVAILABLE,
        "publication_readiness": "dry_run_ready",
        "summary": f"{dry_run.plugin_id} rolled back for local {dry_run.target_client.value} configuration.",
        "action_required": "Review dry-run output before applying again.",
    })
    return PluginOperationResult(
        plugin_id=dry_run.plugin_id,
        target_client=dry_run.target_client,
        version=dry_run.version,
        operation="rollback",
        status="rolled_back",
        writes_performed=writes,
        applied_config_hashes={},
        backup_paths=inventory.backup_paths,
        inventory=updated,
        evidence=f"Rolled back plugin config with approval {approval_ref}.",
    )


def merge_plugin_status_with_events(
    inventory: list[PluginInventoryRecord],
    events: list[ControlPlaneEvent],
) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda event: (event.sequence, event.created_at, event.event_id))
    merged: list[dict[str, Any]] = []
    for record in inventory:
        payload = record.to_status_record().model_dump(mode="json")
        payload["source"] = "inventory"
        payload["last_event_id"] = None
        payload["last_event_at"] = None
        payload["event_derived_status"] = record.dashboard_status.value
        for event in ordered:
            if not _event_references_plugin(event, record.plugin_id):
                continue
            payload["last_event_id"] = event.event_id
            payload["last_event_at"] = event.created_at.isoformat()
            payload["event_derived_status"] = _plugin_status_from_event(event, record.dashboard_status).value
            if event.kind == ControlPlaneEventKind.APPROVAL_RECORDED and event.status == ControlPlaneEventStatus.PASSED:
                payload["publication_readiness"] = "local_ready"
            if event.status in {ControlPlaneEventStatus.FAILED, ControlPlaneEventStatus.BLOCKED}:
                payload["status"] = PluginStatus.FAILED.value
                payload["action_required"] = event.summary
            elif event.status == ControlPlaneEventStatus.NEEDS_HUMAN:
                payload["status"] = PluginStatus.NEEDS_REVIEW.value
                payload["action_required"] = event.summary
            elif payload["event_derived_status"] == PluginStatus.INSTALLED.value:
                payload["status"] = PluginStatus.INSTALLED.value
        merged.append(payload)
    return merged


def build_governance_dashboard_summary(work_items: list[WorkItem]) -> dict[str, Any]:
    governed = [item for item in work_items if item.governance is not None]
    if not governed:
        return {
            "work_item_count": 0,
            "average_mees": None,
            "token_budget_total": 0,
            "approval_required_count": 0,
            "high_risk_count": 0,
            "live_service_opt_in_count": 0,
        }
    mees_values = [item.governance.mees_estimate for item in governed if item.governance is not None]
    return {
        "work_item_count": len(governed),
        "average_mees": round(sum(mees_values) / len(mees_values), 2),
        "token_budget_total": sum(item.governance.token_budget for item in governed if item.governance is not None),
        "approval_required_count": sum(
            1 for item in governed if item.governance is not None and item.governance.approval_required
        ),
        "high_risk_count": sum(
            1 for item in governed if item.governance is not None and item.governance.high_risk
        ),
        "live_service_opt_in_count": sum(
            1 for item in governed if item.governance is not None and item.governance.live_service_opt_in
        ),
    }


def _event_references_plugin(event: ControlPlaneEvent, plugin_id: str) -> bool:
    if event.task_id == plugin_id:
        return True
    lowered = event.summary.lower()
    return plugin_id.lower() in lowered or f"plugin {plugin_id.lower()}" in lowered


def _plugin_status_from_event(event: ControlPlaneEvent, fallback: PluginStatus) -> PluginStatus:
    if event.status == ControlPlaneEventStatus.FAILED:
        return PluginStatus.FAILED
    if event.status == ControlPlaneEventStatus.BLOCKED:
        return PluginStatus.FAILED
    if event.status == ControlPlaneEventStatus.NEEDS_HUMAN:
        return PluginStatus.NEEDS_REVIEW
    if event.kind in {ControlPlaneEventKind.DEPLOYMENT_EVENT, ControlPlaneEventKind.DEPLOYMENT}:
        if event.status == ControlPlaneEventStatus.PASSED:
            return PluginStatus.INSTALLED
    if "uninstalled" in event.summary.lower():
        return PluginStatus.AVAILABLE
    if "applied" in event.summary.lower() and event.status == ControlPlaneEventStatus.PASSED:
        return PluginStatus.INSTALLED
    return fallback


def seed_plugin_statuses() -> tuple[PluginStatusRecord, ...]:
    return (
        PluginStatusRecord(
            plugin_id="vad-codex-local",
            target_client=PluginTargetClient.CODEX,
            status=PluginStatus.INSTALLED,
            version="1.0.0",
            local_version="1.0.0",
            publication_readiness="local_ready",
            summary="Codex local MCP plugin is installed for this workspace.",
        ),
        PluginStatusRecord(
            plugin_id="vad-claude-code-local",
            target_client=PluginTargetClient.CLAUDE_CODE,
            status=PluginStatus.AVAILABLE,
            version="1.0.0",
            local_version="1.0.0",
            publication_readiness="dry_run_ready",
            summary="Claude Code local MCP plugin package is available for dry-run review.",
        ),
        PluginStatusRecord(
            plugin_id="vad-vscode-local",
            target_client=PluginTargetClient.VS_CODE,
            status=PluginStatus.NEEDS_REVIEW,
            version="1.0.0",
            local_version="1.0.0",
            publication_readiness="needs_operator_review",
            summary="VS Code workspace plugin config requires operator review before install.",
            action_required="Review generated workspace settings before applying.",
        ),
        PluginStatusRecord(
            plugin_id="vad-cursor-local",
            target_client=PluginTargetClient.CURSOR,
            status=PluginStatus.FAILED,
            version="1.0.0",
            local_version="1.0.0",
            publication_readiness="blocked",
            summary="Cursor plugin install check failed during local validation.",
            action_required="Inspect dry-run output and retry after config review.",
        ),
    )


def _plugin_config_content(manifest: VADPluginManifest, artifact: PluginArtifactVerification) -> dict:
    return {
        "plugin_id": manifest.plugin_id,
        "target_client": manifest.target_client.value,
        "version": manifest.version,
        "command": manifest.command.model_dump(mode="json"),
        "permissions": [permission.model_dump(mode="json") for permission in manifest.permissions],
        "tools": [tool.model_dump(mode="json") for tool in manifest.tools],
        "prompts": [prompt.model_dump(mode="json") for prompt in manifest.prompts],
        "artifact_digest": artifact.artifact_digest,
        "manifest_digest": artifact.manifest_digest,
    }


def sign_plugin_artifact_hash(
    artifact_hash: PluginArtifactHash,
    signer: LocalDevelopmentSigner,
) -> PluginArtifactSignature:
    return PluginArtifactSignature(signature=signer.sign_payload(artifact_hash.model_dump(mode="json")))


def verify_plugin_artifact(
    manifest: VADPluginManifest,
    *,
    signature: PluginArtifactSignature | None = None,
    signer: LocalDevelopmentSigner | None = None,
) -> PluginArtifactVerification:
    artifact_hash = compute_plugin_artifact_hash(manifest)
    signature_verified = False
    signer_key_id = None
    if signature is not None:
        signer_key_id = signature.signature.key_id
        if signer is not None:
            signature_verified = signer.verify_payload(artifact_hash.model_dump(mode="json"), signature.signature)
    return PluginArtifactVerification(
        plugin_id=artifact_hash.plugin_id,
        version=artifact_hash.version,
        artifact_digest=artifact_hash.artifact_digest,
        manifest_digest=artifact_hash.manifest_digest,
        file_count=len(artifact_hash.file_digests),
        signature_present=signature is not None,
        signature_verified=signature_verified,
        signer_key_id=signer_key_id,
        evidence="Plugin artifact digest verified." if signature is None else (
            "Plugin artifact signature verified." if signature_verified else "Plugin artifact signature verification failed."
        ),
    )


SECRET_MARKERS = ("api_key", "token", "password", "secret", "private_key", "sk-")


def _validate_identifier(value: str, label: str) -> str:
    _validate_text(value, label)
    if any(separator in value for separator in ["/", "\\"]):
        raise ValueError(f"{label} must not contain path separators")
    return value


def _validate_text(value: str, label: str) -> None:
    if any(separator in value for separator in ["\n", "\r", "\t"]):
        raise ValueError(f"{label} must not contain control separators")


def _looks_secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def _validate_relative_path(value: str, label: str) -> str:
    _validate_text(value, label)
    if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
        raise ValueError(f"{label} must be relative")
    return value


def _resolve_under(root: Path, relative_path: str, label: str) -> Path:
    _validate_relative_path(relative_path, label)
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay under {root}") from exc
    return target


def _audit_finding(check: str, detail: str) -> PluginSecurityAuditFinding:
    return PluginSecurityAuditFinding(check=check, detail=detail)


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _resolved_roots(workspace_root: Path, user_config_root: Path) -> dict[PluginConfigScope, Path]:
    return {
        PluginConfigScope.WORKSPACE: workspace_root.resolve(),
        PluginConfigScope.USER: user_config_root.resolve(),
    }


def _validate_approval_ref(value: str) -> None:
    _validate_text(value, "plugin operation approval")
    if not value.strip():
        raise ValueError("plugin operation requires explicit approval evidence")


def _validate_apply_plan(
    dry_run: PluginInstallerDryRun,
    roots: dict[PluginConfigScope, Path],
) -> None:
    if not dry_run.dry_run or dry_run.writes_performed != 0:
        raise ValueError("plugin apply requires a no-write dry-run plan")
    if len(dry_run.operations) != len(dry_run.rollback):
        raise ValueError("plugin apply requires rollback evidence for every operation")
    for operation, rollback in zip(dry_run.operations, dry_run.rollback):
        if operation.action != PluginInstallAction.WRITE_CONFIG:
            raise ValueError(f"unsupported plugin install action {operation.action.value}")
        _guarded_operation_path(operation, roots)
        _guarded_backup_path(rollback, operation, roots)


def _validate_inventory_matches_plan(
    dry_run: PluginInstallerDryRun,
    inventory: PluginInventoryRecord,
) -> None:
    if inventory.plugin_id != dry_run.plugin_id:
        raise ValueError("plugin inventory does not match dry-run plugin id")
    if inventory.target_client != dry_run.target_client:
        raise ValueError("plugin inventory does not match dry-run target client")
    if inventory.version != dry_run.version:
        raise ValueError("plugin inventory does not match dry-run version")
    if inventory.artifact_digest and inventory.artifact_digest != dry_run.artifact.artifact_digest:
        raise ValueError("plugin inventory does not match dry-run artifact digest")
    if inventory.manifest_digest and inventory.manifest_digest != dry_run.artifact.manifest_digest:
        raise ValueError("plugin inventory does not match dry-run manifest digest")


def _guarded_operation_path(
    operation: PluginInstallOperation,
    roots: dict[PluginConfigScope, Path],
) -> Path:
    if operation.scope not in roots:
        raise ValueError("plugin writers are limited to user and workspace config roots")
    target = Path(operation.path).resolve()
    if not _path_is_under(target, roots[operation.scope]):
        raise ValueError(f"plugin operation path escapes approved {operation.scope.value} root")
    return target


def _guarded_backup_path(
    rollback: PluginRollbackOperation,
    operation: PluginInstallOperation,
    roots: dict[PluginConfigScope, Path],
) -> Path:
    if rollback.action != "restore_or_remove":
        raise ValueError(f"unsupported plugin rollback action {rollback.action}")
    if rollback.path != operation.path:
        raise ValueError("plugin rollback path must match install operation path")
    if rollback.backup_path is None:
        raise ValueError("plugin rollback evidence requires a backup path")
    backup = Path(rollback.backup_path).resolve()
    if not _path_is_under(backup, roots[operation.scope]):
        raise ValueError(f"plugin rollback backup path escapes approved {operation.scope.value} root")
    return backup


def _inventory_path(
    path: Path,
    scope: PluginConfigScope,
    roots: dict[PluginConfigScope, Path],
) -> str:
    relative = path.resolve().relative_to(roots[scope]).as_posix()
    return f"{scope.value}/{relative}"


def _canonical_json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _walk_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_values(item)
    elif value is not None:
        yield value


def _manifest_scan_values(manifest: VADPluginManifest):
    yield manifest.plugin_id
    yield manifest.version
    yield manifest.command.executable
    yield from manifest.command.args
    for key, value in manifest.command.env.items():
        yield key
        yield value
    for permission in manifest.permissions:
        yield permission.name
        yield permission.reason
    for tool in manifest.tools:
        yield tool.name
        yield tool.role
    for prompt in manifest.prompts:
        yield prompt.prompt_id
        yield prompt.role
        yield prompt.path
    for path in manifest.checksums:
        yield path


def _has_non_local_http_endpoint(value: str) -> bool:
    lowered = value.lower()
    for scheme in ("http://", "https://"):
        start = lowered.find(scheme)
        while start >= 0:
            rest = lowered[start + len(scheme):]
            host = rest.split("/", 1)[0].split(":", 1)[0]
            if host not in {"localhost", "127.0.0.1", "::1"}:
                return True
            start = lowered.find(scheme, start + len(scheme))
    return False
