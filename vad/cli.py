import argparse
import json
import sys
import yaml
from pathlib import Path
from pydantic import ValidationError
from vad.contracts.models import (
    AutonomyTier,
    Constraints,
    EIP,
    Goal,
    Invariants,
    ModelBudget,
    ReleaseRequirements,
    RiskTier,
    TelemetryRequirements,
    ToolPermissions,
)

def load_structured_file(file_path: Path):
    with open(file_path, "r") as f:
        if file_path.suffix in [".yml", ".yaml"]:
            return yaml.safe_load(f)
        return json.load(f)

def init_command(args):
    from vad.ask.assessment import AskAssessment, eip_from_assessment
    from vad.contracts.normalize import normalize_eip

    out_path = Path(args.out)
    if out_path.exists() and not args.force:
        print(f"File {args.out} already exists. Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    if args.from_assessment:
        assessment = AskAssessment(**load_structured_file(Path(args.from_assessment)))
        eip = eip_from_assessment(args.name, assessment)
    else:
        eip = EIP(
            version="1.0.0",
            name=args.name,
            goal=Goal(
                description=f"Describe the intended outcome for {args.name}.",
                success_criteria=["Define at least one executable success criterion."],
            ),
            non_goals=[],
            risk_tier=RiskTier.LOW,
            autonomy_tier=AutonomyTier.ASSISTED,
            scope_boundaries=[],
            invariants=Invariants(),
            constraints=Constraints(),
            proof_obligations=[],
            tool_permissions=ToolPermissions(allowed=["pytest"], denied=["network"]),
            memory_requirements=[],
            model_budget=ModelBudget(max_tokens=10000, max_cost=1.0, max_loop_depth=3),
            release_requirements=ReleaseRequirements(required=False, gates=[]),
            telemetry_requirements=TelemetryRequirements(required=False, signals=[]),
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(normalize_eip(eip), sort_keys=False))
    print(f"Created EIP template at {out_path}")

def validate_command(args):
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)
        
    try:
        data = load_structured_file(file_path)
        EIP(**data)
        print("EIP is valid.")
    except ValidationError as e:
        print("EIP validation failed:", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading file: {e}", file=sys.stderr)
        sys.exit(1)

def diff_command(args):
    from vad.contracts.diff import diff_eips, format_diff

    old_path = Path(args.old_file)
    new_path = Path(args.new_file)
    if not old_path.exists():
        print(f"File {args.old_file} not found.", file=sys.stderr)
        sys.exit(1)
    if not new_path.exists():
        print(f"File {args.new_file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        changes = diff_eips(EIP(**load_structured_file(old_path)), EIP(**load_structured_file(new_path)))
        if args.json:
            print(json.dumps({"changes": changes}, indent=2))
        else:
            sys.stdout.write(format_diff(changes))
    except ValidationError as e:
        print("EIP diff failed:", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error diffing files: {e}", file=sys.stderr)
        sys.exit(1)

def ask_assess_command(args):
    from vad.ask.classifier import assess_ask

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)

    assessment = assess_ask(file_path.read_text())
    payload = assessment.model_dump(mode="json")
    output = json.dumps(payload, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(output)
    elif args.json:
        sys.stdout.write(output)
    else:
        print(f"Summary: {assessment.summary}")
        print(f"Risk: {assessment.risk_tier.value}")
        print(f"Autonomy: {assessment.autonomy_tier.value}")
        print(f"Effort: {assessment.effort_type.value}")
        if assessment.clarification_questions:
            print("Clarification questions:")
            for question in assessment.clarification_questions:
                print(f"- {question}")

def proof_map_command(args):
    from vad.proof.mapper import map_proofs

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        plan = map_proofs(EIP(**load_structured_file(file_path)))
        output = yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False)
        if args.out:
            out_path = Path(args.out)
            if out_path.exists() and not args.force:
                print(f"File {args.out} already exists. Use --force to overwrite.", file=sys.stderr)
                sys.exit(1)
            out_path.write_text(output)
        else:
            sys.stdout.write(output)
    except (ValidationError, ValueError) as e:
        print(f"Proof mapping failed: {e}", file=sys.stderr)
        sys.exit(1)

def loop_run_command(args):
    from vad.loop.orchestrator import VADOrchestrator
    from vad.loop.state import LoopStatus
    from vad.proof.plan import ProofPlan

    eip_path = Path(args.eip_file)
    proof_path = Path(args.proof_plan_file)
    if not eip_path.exists():
        print(f"File {args.eip_file} not found.", file=sys.stderr)
        sys.exit(1)
    if not proof_path.exists():
        print(f"File {args.proof_plan_file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        result = VADOrchestrator().run(
            EIP(**load_structured_file(eip_path)),
            ProofPlan(**load_structured_file(proof_path)),
            builder=args.builder,
            verifier=args.verifier,
        )
        output = json.dumps(result.model_dump(mode="json"), indent=2) + "\n"
        if args.out:
            Path(args.out).write_text(output)
        else:
            sys.stdout.write(output)
        if result.final_decision != LoopStatus.PASSED:
            sys.exit(1)
    except ValidationError as e:
        print(f"Loop run failed: {e}", file=sys.stderr)
        sys.exit(1)

def evidence_inspect_command(args):
    from vad.evidence.bundle import EvidenceBundle, RunEvidence

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        payload = load_structured_file(file_path)
        evidence_data = payload.get("evidence", payload) if isinstance(payload, dict) else payload
        expected_hash = payload.get("evidence_hash") if isinstance(payload, dict) else None
        evidence = RunEvidence(**evidence_data)
        bundle = EvidenceBundle(evidence)
        digest = bundle.compute_hash()
        if expected_hash and bundle.is_tampered(expected_hash):
            print("Evidence tamper check failed.", file=sys.stderr)
            sys.exit(1)
        print(f"Run: {evidence.run_id}")
        print(f"Decision: {evidence.final_decision}")
        print(f"Hash: {digest}")
        print(f"Verification: {_verification_summary(evidence)}")
        print(f"MEES: {evidence.effort.mees} ({evidence.effort.policy})")
        print(f"Token budget: {evidence.tokens.budget}")
    except ValidationError as e:
        print(f"Evidence validation failed: {e}", file=sys.stderr)
        sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"Error inspecting evidence: {e}", file=sys.stderr)
        sys.exit(1)

def _verification_summary(evidence):
    if evidence.verification is None:
        return "not_run"
    return "passed" if evidence.verification.passed else "failed"

def effort_score_command(args):
    from vad.effort import scoring

    try:
        diff_metrics = scoring.collect_git_diff_metrics(before=args.before, after=args.after)
        diff_penalties = scoring.penalties_from_diff_metrics(diff_metrics)
        quality_metrics = scoring.quality_metrics_from_supplied()
        quality_penalties = scoring.penalties_from_quality_metrics(quality_metrics)
        penalties = scoring.MeesPenalties(
            complexity=quality_penalties.complexity,
            maintainability=quality_penalties.maintainability,
            diff=diff_penalties.diff,
            spread=diff_penalties.spread,
            dependency=diff_penalties.dependency,
        )
        result = scoring.score_effort(
            args.type,
            penalties,
            metric_warnings=diff_metrics.warnings + quality_metrics.warnings,
        )
        payload = {
            **result.model_dump(mode="json"),
            "metrics": {
                "changed_files": diff_metrics.changed_files,
                "insertions": diff_metrics.insertions,
                "deletions": diff_metrics.deletions,
                "dependency_files_changed": diff_metrics.dependency_files_changed,
            },
        }
        if args.readable:
            print(f"MEES: {result.score} ({result.policy})")
            print(f"Effort: {result.effort_type.value}")
            print(f"Changed files: {diff_metrics.changed_files}")
            print(f"Line delta: {diff_metrics.line_delta}")
        else:
            print(json.dumps(payload, indent=2))
        if not args.warn_only and result.policy == "block":
            sys.exit(1)
        if not args.warn_only and result.policy == "warn":
            sys.exit(2)
    except ValueError as e:
        print(f"Effort scoring failed: {e}", file=sys.stderr)
        sys.exit(1)

def normalize_command(args):
    from vad.contracts.normalize import normalize_eip

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        eip = EIP(**load_structured_file(file_path))
        normalized = normalize_eip(eip)
        if args.json:
            output = json.dumps(normalized, indent=2, sort_keys=False) + "\n"
        else:
            output = yaml.safe_dump(normalized, sort_keys=False)

        if args.out:
            out_path = Path(args.out)
            if out_path.exists() and not args.force:
                print(f"File {args.out} already exists. Use --force to overwrite.", file=sys.stderr)
                sys.exit(1)
            out_path.write_text(output)
        else:
            sys.stdout.write(output)
    except ValidationError as e:
        print("EIP normalization failed:", file=sys.stderr)
        print(e, file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error normalizing file: {e}", file=sys.stderr)
        sys.exit(1)

def retro_command(args):
    from vad.evidence.bundle import EvidenceBundle
    from vad.feedback.retro import RetroAnalyzer
    from vad.memory.gateway import MemoryGateway
    from vad.memory.stores.local import LocalMemoryStore
    from vad.memory.redaction import Redactor
    
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"File {args.file} not found.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(file_path, "r") as f:
            if file_path.suffix in [".yml", ".yaml"]:
                data = yaml.safe_load(f)
            else:
                import json
                data = json.load(f)
        
        bundle = EvidenceBundle(data)
        gateway = MemoryGateway(store=LocalMemoryStore(), redactor=Redactor())
        analyzer = RetroAnalyzer(gateway)
        
        result = analyzer.analyze(bundle)
        print("Retro analysis complete.")
        print(f"Learnings: {result['learning']}")
    except Exception as e:
        print(f"Error processing retro analysis: {e}", file=sys.stderr)
        sys.exit(1)

def repo_assess_command(args):
    from vad.repo.intake import RepositoryIntakeError, assess_repository

    try:
        intake = assess_repository(Path(args.path))
        print(json.dumps(intake.model_dump(mode="json"), indent=2))
    except RepositoryIntakeError as e:
        print(f"Repository assessment failed: {e}", file=sys.stderr)
        sys.exit(1)

def sign_evidence_command(args):
    from vad.signing.local import LocalDevelopmentSigner

    evidence_path = Path(args.evidence_file)
    secret_path = Path(args.secret_file)
    for file_path in (evidence_path, secret_path):
        if not file_path.exists():
            print(f"File {file_path} not found.", file=sys.stderr)
            sys.exit(1)

    payload = load_structured_file(evidence_path)
    signer = LocalDevelopmentSigner(args.key_id, secret_path.read_bytes())
    envelope = signer.sign_payload(payload)
    _write_or_print_json({
        "payload": payload,
        "signature": envelope.model_dump(mode="json"),
    }, args.out)

def sign_verify_command(args):
    from vad.signing.local import LocalDevelopmentSigner
    from vad.signing.models import SignatureEnvelope

    signed_path = Path(args.signed_file)
    secret_path = Path(args.secret_file)
    for file_path in (signed_path, secret_path):
        if not file_path.exists():
            print(f"File {file_path} not found.", file=sys.stderr)
            sys.exit(1)

    try:
        signed = load_structured_file(signed_path)
        envelope = SignatureEnvelope(**signed["signature"])
        signer = LocalDevelopmentSigner(envelope.key_id, secret_path.read_bytes())
        verified = signer.verify_payload(signed["payload"], envelope)
        _write_or_print_json({
            "verified": verified,
            "key_id": envelope.key_id,
            "payload_digest": envelope.payload_digest,
        }, args.out)
        if not verified:
            sys.exit(1)
    except (KeyError, TypeError, ValidationError, ValueError) as e:
        print(f"Signature verification failed: {e}", file=sys.stderr)
        sys.exit(1)

def swarm_run_command(args):
    if args.fixture:
        from vad.swarm.demo import run_level3_demo_swarm

        if not args.workdir:
            print("--workdir is required when --fixture is supplied.", file=sys.stderr)
            sys.exit(1)
        result = run_level3_demo_swarm(
            args.run_id,
            Path(args.fixture),
            Path(args.workdir),
            Path(args.state) if args.state else None,
        )
        _write_or_print_json(result.model_dump(mode="json"), args.out)
        return

    from vad.swarm.agents import AgentCard, AgentRole
    from vad.swarm.coordinator import LocalSwarmCoordinator
    from vad.swarm.state import SwarmState
    from vad.swarm.tasks import SwarmTask, SwarmTaskGraph

    agents = [
        AgentCard(agent_id="planner", role=AgentRole.PLANNER, capabilities=["plan"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="builder", role=AgentRole.BUILDER, capabilities=["modify_code"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="verifier", role=AgentRole.VERIFIER, capabilities=["verify"], model_tiers=["tier1"], autonomy_limit=1),
        AgentCard(agent_id="auditor", role=AgentRole.AUDITOR, capabilities=["audit_evidence"], model_tiers=["tier1"], autonomy_limit=1),
    ]
    graph = SwarmTaskGraph(tasks=[
        SwarmTask(task_id="plan", role=AgentRole.PLANNER, description="Plan the requested work."),
        SwarmTask(task_id="build", role=AgentRole.BUILDER, description="Build the planned change.", depends_on=["plan"]),
        SwarmTask(task_id="verify", role=AgentRole.VERIFIER, description="Verify the build.", depends_on=["build"]),
        SwarmTask(task_id="audit", role=AgentRole.AUDITOR, description="Audit produced evidence.", depends_on=["verify"]),
    ])
    coordinator = LocalSwarmCoordinator(agents)
    all_messages = []
    completed = []
    for _ in graph.tasks:
        result = coordinator.run_ready_tasks(graph)
        all_messages.extend(result.messages)
        completed.extend(result.completed_task_ids)

    state = SwarmState(run_id=args.run_id, graph=graph, messages=all_messages)
    if args.state:
        state.save(args.state)
    _write_or_print_json({
        "run_id": state.run_id,
        "completed_task_ids": completed,
        "messages": [message.model_dump(mode="json") for message in all_messages],
        "final_decision": "passed" if len(completed) == len(graph.tasks) else "blocked",
    }, args.out)

def swarm_status_command(args):
    from vad.swarm.state import SwarmState

    state = SwarmState.load(args.state)
    _write_or_print_json({
        "run_id": state.run_id,
        "tasks": [task.model_dump(mode="json") for task in state.graph.tasks],
        "messages": [message.model_dump(mode="json") for message in state.messages],
        "final_decision": "passed" if all(task.status.value == "completed" for task in state.graph.tasks) else "blocked",
    }, args.out)

def deploy_plan_command(args):
    from vad.deploy.models import DeploymentPlan, DeploymentTarget

    eip_path = Path(args.eip_file)
    target_path = Path(args.target_file)
    for file_path in (eip_path, target_path):
        if not file_path.exists():
            print(f"File {file_path} not found.", file=sys.stderr)
            sys.exit(1)

    try:
        eip = EIP(**load_structured_file(eip_path))
        target = DeploymentTarget(**load_structured_file(target_path))
        plan = DeploymentPlan(
            plan_id=args.plan_id or f"{eip.name}-{target.target_id}",
            target=target,
            approval_ref=args.approval_ref,
            evidence_ref=args.evidence_ref,
        )
        output = yaml.safe_dump(plan.model_dump(mode="json"), sort_keys=False)
        if args.out:
            Path(args.out).write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
    except ValidationError as e:
        print(f"Deployment plan failed: {e}", file=sys.stderr)
        sys.exit(1)

def deploy_dry_run_command(args):
    from vad.deploy.models import DeploymentPlan
    from vad.deploy.providers.fake import FakeDeploymentProvider

    plan_path = Path(args.deployment_plan)
    if not plan_path.exists():
        print(f"File {args.deployment_plan} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        plan = DeploymentPlan(**load_structured_file(plan_path))
        provider = FakeDeploymentProvider()
        _write_or_print_json(provider.dry_run(plan), args.out)
    except ValidationError as e:
        print(f"Deployment dry-run failed: {e}", file=sys.stderr)
        sys.exit(1)

def deploy_apply_command(args):
    from vad.deploy.models import DeploymentPlan
    from vad.deploy.providers.fake import FakeDeploymentProvider
    from vad.policy.engine import PolicyEngine

    plan_path = Path(args.deployment_plan)
    if not plan_path.exists():
        print(f"File {args.deployment_plan} not found.", file=sys.stderr)
        sys.exit(1)

    try:
        plan = DeploymentPlan(**load_structured_file(plan_path))
        approval_ref = args.approval_ref or plan.approval_ref
        decision = PolicyEngine().evaluate_deployment(
            action="deploy_apply",
            environment=plan.target.environment.value,
            approval_ref=approval_ref,
            telemetry_count=len(plan.target.telemetry),
            rollback_enabled=plan.target.rollback.enabled,
        )
        if not decision.allow:
            _write_or_print_json({"decision": decision.model_dump(mode="json")}, args.out)
            sys.exit(1)
        provider = FakeDeploymentProvider()
        record = provider.apply(plan)
        _write_or_print_json({"decision": decision.model_dump(mode="json"), "deployment": record}, args.out)
    except ValidationError as e:
        print(f"Deployment apply failed: {e}", file=sys.stderr)
        sys.exit(1)

def deploy_rollback_command(args):
    from vad.deploy.providers.fake import FakeDeploymentProvider
    from vad.policy.engine import PolicyEngine

    record_path = Path(args.deployment_record)
    if not record_path.exists():
        print(f"File {args.deployment_record} not found.", file=sys.stderr)
        sys.exit(1)

    record_payload = load_structured_file(record_path)
    record = record_payload.get("deployment", record_payload)
    decision = PolicyEngine().evaluate_deployment(
        action="deploy_rollback",
        environment=record.get("environment", "production"),
        rollback_approval_ref=args.rollback_approval_ref,
    )
    if not decision.allow:
        _write_or_print_json({"decision": decision.model_dump(mode="json")}, args.out)
        sys.exit(1)

    provider = FakeDeploymentProvider()
    deployment_id = record["deployment_id"]
    provider.deployments[deployment_id] = dict(record)
    if record.get("artifact_digest"):
        provider.targets[record["target_id"]] = record["artifact_digest"]
    rollback = provider.rollback(deployment_id)
    _write_or_print_json({"decision": decision.model_dump(mode="json"), "rollback": rollback}, args.out)

def deploy_demo_command(args):
    from vad.deploy.demo import run_signed_deployment_demo

    try:
        result = run_signed_deployment_demo(Path(args.fixture), Path(args.out_dir), key_id=args.key_id)
        _write_or_print_json(result.model_dump(mode="json"), args.out)
    except (FileNotFoundError, ValidationError, ValueError) as e:
        print(f"Deployment demo failed: {e}", file=sys.stderr)
        sys.exit(1)

def deploy_failure_demo_command(args):
    from vad.deploy.demo import run_failed_deployment_demo

    try:
        result = run_failed_deployment_demo(
            Path(args.fixture),
            Path(args.out_dir),
            db_path=Path(args.db) if args.db else None,
        )
        _write_or_print_json(result.model_dump(mode="json"), args.out)
    except (FileNotFoundError, ValidationError, ValueError) as e:
        print(f"Deployment failure demo failed: {e}", file=sys.stderr)
        sys.exit(1)

def repo_patch_command(args):
    from vad.repo.dependencies import assess_dependency_changes
    from vad.repo.git import inspect_git_state
    from vad.repo.intake import RepositoryIntakeError
    from vad.repo.patch_apply import apply_unified_diff
    from vad.proof.plan import ProofPlan
    from vad.verify.runner import VerifierRunner

    repo_path = Path(args.path)
    eip_path = Path(args.eip_file)
    proof_path = Path(args.proof_plan_file)
    patch_path = Path(args.patch)
    for file_path in (eip_path, proof_path, patch_path):
        if not file_path.exists():
            print(f"File {file_path} not found.", file=sys.stderr)
            sys.exit(1)

    try:
        git_state = inspect_git_state(repo_path, allow_dirty=args.allow_dirty)
        if not git_state.autonomous_patch_allowed:
            print(f"Repository patch blocked: {git_state.blocker}", file=sys.stderr)
            sys.exit(1)

        eip = EIP(**load_structured_file(eip_path))
        plan = ProofPlan(**load_structured_file(proof_path))
        apply_result = apply_unified_diff(repo_path, patch_path.read_text(encoding="utf-8"))
        if not apply_result.applied:
            print(f"Repository patch failed: {apply_result.blocker}", file=sys.stderr)
            sys.exit(1)

        dependency_assessment = assess_dependency_changes(
            apply_result.changed_files,
            approved=args.approve_dependencies,
        )
        dependency_decision = dependency_assessment.decision()
        rollback = None
        verification = None

        if not dependency_decision.allow:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            payload = _repo_patch_payload(apply_result, dependency_decision, verification, rollback)
            _write_or_print_json(payload, args.out)
            sys.exit(1)

        verification = VerifierRunner(eip, plan, cwd=str(repo_path.resolve())).run()
        if not verification.passed:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            payload = _repo_patch_payload(apply_result, dependency_decision, verification, rollback)
            _write_or_print_json(payload, args.out)
            sys.exit(1)

        payload = _repo_patch_payload(apply_result, dependency_decision, verification, rollback)
        _write_or_print_json(payload, args.out)
    except (RepositoryIntakeError, ValidationError, ValueError) as e:
        print(f"Repository patch failed: {e}", file=sys.stderr)
        sys.exit(1)

def repo_run_command(args):
    from vad.ask.assessment import eip_from_assessment
    from vad.ask.classifier import assess_ask
    from vad.proof.plan import ProofMapping, ProofPlan, compute_eip_digest
    from vad.repo.dependencies import assess_dependency_changes
    from vad.repo.git import inspect_git_state
    from vad.repo.intake import RepositoryIntakeError, assess_repository
    from vad.repo.patch_apply import apply_unified_diff
    from vad.repo.patch_plan import build_patch_plan
    from vad.repo.proof_discovery import discover_proof_commands
    from vad.verify.runner import VerifierRunner

    repo_path = Path(args.path)
    ask_path = Path(args.ask_file)
    patch_path = Path(args.patch)
    for file_path in (ask_path, patch_path):
        if not file_path.exists():
            print(f"File {file_path} not found.", file=sys.stderr)
            sys.exit(1)

    try:
        intake = assess_repository(repo_path)
        git_state = inspect_git_state(repo_path, allow_dirty=args.allow_dirty)
        if not git_state.autonomous_patch_allowed:
            _write_or_print_json({"decision": "blocked", "blocker": git_state.blocker}, args.out)
            sys.exit(1)

        assessment = assess_ask(ask_path.read_text(encoding="utf-8"))
        if assessment.blocks_autonomous_execution:
            _write_or_print_json({
                "decision": "blocked",
                "blocker": "ask assessment requires human clarification or manual autonomy",
                "assessment": assessment.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
            }, args.out)
            sys.exit(1)

        discovery = discover_proof_commands(repo_path)
        if not discovery.has_proof_commands:
            _write_or_print_json({
                "decision": "blocked",
                "blocker": discovery.blocker,
                "assessment": assessment.model_dump(mode="json"),
                "intake": intake.model_dump(mode="json"),
            }, args.out)
            sys.exit(1)

        apply_result = apply_unified_diff(repo_path, patch_path.read_text(encoding="utf-8"))
        if not apply_result.applied:
            _write_or_print_json({"decision": "failed", "blocker": apply_result.blocker}, args.out)
            sys.exit(1)

        scope_boundaries = args.scope or _scope_from_paths(apply_result.changed_files)
        eip = eip_from_assessment(repo_path.name, assessment).model_copy(update={"scope_boundaries": scope_boundaries})
        patch_plan = build_patch_plan(eip, apply_result.changed_files, discovery.commands, assessment.mees_budget.minimum_score)
        dependency_decision = assess_dependency_changes(
            apply_result.changed_files,
            approved=args.approve_dependencies,
        ).decision()
        rollback = None
        verification = None

        if patch_plan.blocker or not dependency_decision.allow:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            payload = _repo_run_payload(intake, assessment, patch_plan, apply_result, dependency_decision, verification, rollback)
            _write_or_print_json(payload, args.out)
            sys.exit(1)

        proof_command = " ".join(discovery.commands[0].command)
        proof_plan = ProofPlan(
            eip_version=eip.version,
            eip_digest=compute_eip_digest(eip),
            mappings=[
                ProofMapping(obligation_id=obligation.id, test_command=proof_command)
                for obligation in eip.proof_obligations
            ],
        )
        verification = VerifierRunner(eip, proof_plan, cwd=str(repo_path.resolve())).run()
        if not verification.passed:
            rollback = apply_result.journal.rollback() if apply_result.journal else None
            payload = _repo_run_payload(intake, assessment, patch_plan, apply_result, dependency_decision, verification, rollback)
            _write_or_print_json(payload, args.out)
            sys.exit(1)

        payload = _repo_run_payload(intake, assessment, patch_plan, apply_result, dependency_decision, verification, rollback)
        _write_or_print_json(payload, args.out)
    except (RepositoryIntakeError, ValidationError, ValueError) as e:
        print(f"Repository run failed: {e}", file=sys.stderr)
        sys.exit(1)

def _repo_patch_payload(apply_result, dependency_decision, verification, rollback):
    return {
        "applied": apply_result.applied,
        "changed_files": apply_result.changed_files,
        "journal": apply_result.journal.to_evidence(rollback).model_dump(mode="json") if apply_result.journal else None,
        "dependency_decision": dependency_decision.model_dump(mode="json"),
        "verification": verification.model_dump(mode="json") if verification else None,
        "rolled_back": rollback.rolled_back if rollback else False,
        "blocker": apply_result.blocker or (rollback.blocker if rollback else None),
    }

def _write_or_print_json(payload, out_path):
    output = json.dumps(payload, indent=2) + "\n"
    if out_path:
        Path(out_path).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)

def _repo_run_payload(intake, assessment, patch_plan, apply_result, dependency_decision, verification, rollback):
    return {
        "decision": "passed" if verification and verification.passed else "blocked",
        "intake": intake.model_dump(mode="json"),
        "assessment": assessment.model_dump(mode="json"),
        "patch_plan": patch_plan.model_dump(mode="json"),
        "patch": _repo_patch_payload(apply_result, dependency_decision, verification, rollback),
    }

def _scope_from_paths(paths):
    scopes = sorted({path.replace("\\", "/").split("/", 1)[0] for path in paths if path})
    return scopes or ["."]

def mcp_run_command(args):
    from vad.adapters.mcp import serve
    serve()

def ui_serve_command(args):
    from vad.server.serve import serve_ui

    serve_ui(
        args.host,
        args.port,
        Path(args.evidence_root),
        Path(args.db),
        Path(args.ui_root),
        seed_demo=args.seed_demo,
        seed_level3_demo=args.seed_level3_demo,
        seed_multi_client_simulator=args.seed_multi_client_simulator,
    )


def local_os_demo_command(args):
    from vad.control_plane.config import ControlPlaneConfig
    from vad.control_plane.server import run_control_plane

    config = ControlPlaneConfig(
        bind_host=args.host,
        port=args.port,
        db_path=Path(args.db),
        evidence_root=Path(args.evidence_root),
        ui_root=Path(args.ui_root),
        plugin_root=Path(args.plugin_root),
        allow_non_local_bind=args.allow_non_local_bind,
    )
    run_control_plane(
        config,
        seed_multi_client_simulator=True,
        serve_forever=not args.test_start,
    )


def control_plane_serve_command(args):
    import os
    from vad.control_plane.config import ControlPlaneConfig
    from vad.control_plane.server import run_control_plane

    overrides = {}
    for attr, field_name in [
        ("host", "bind_host"),
        ("port", "port"),
        ("config", "config_path"),
        ("db", "db_path"),
        ("evidence_root", "evidence_root"),
        ("ui_root", "ui_root"),
        ("plugin_root", "plugin_root"),
        ("log_level", "log_level"),
    ]:
        value = getattr(args, attr)
        if value is not None:
            overrides[field_name] = Path(value) if field_name.endswith(("_path", "_root")) or field_name == "config_path" else value
    if args.allow_non_local_bind:
        overrides["allow_non_local_bind"] = True

    config = ControlPlaneConfig.from_env(os.environ, **overrides)
    run_control_plane(
        config,
        seed_demo=args.seed_demo,
        seed_level3_demo=args.seed_level3_demo,
        seed_multi_client_simulator=args.seed_multi_client_simulator,
        serve_forever=not args.test_start,
    )

def clients_register_command(args):
    import sqlite3
    from vad.control_plane.clients import ClientManifest
    from vad.server.api.clients import ClientRegistryService
    from vad.server.db.store import ServerStore

    store = ServerStore(Path(args.db))
    manifest = ClientManifest(
        client_id=args.client_id,
        display_name=args.display_name,
        client_type=args.client_type,
        version=args.version,
        connection_mode=args.connection_mode,
        supported_capabilities=tuple(args.capability),
        workspace_root=Path(args.workspace_root),
        trust_state=args.trust_state,
    )
    try:
        result = ClientRegistryService(store).register(manifest)
    except sqlite3.IntegrityError:
        _write_or_print_json({"error": "duplicate_client", "client_id": args.client_id}, args.out)
        sys.exit(1)
    _write_or_print_json({
        "client": result.manifest.model_dump(mode="json"),
        "event": result.event.model_dump(mode="json"),
    }, args.out)

def clients_list_command(args):
    from vad.server.api.clients import ClientRegistryService
    from vad.server.db.store import ServerStore

    clients = ClientRegistryService(ServerStore(Path(args.db))).list_statuses()
    _write_or_print_json({"clients": [client.model_dump(mode="json") for client in clients]}, args.out)

def clients_unregister_command(args):
    from vad.server.api.clients import ClientRegistryService
    from vad.server.db.store import ServerStore

    try:
        result = ClientRegistryService(ServerStore(Path(args.db))).unregister(args.client_id)
    except KeyError:
        _write_or_print_json({"error": "client_not_found", "client_id": args.client_id}, args.out)
        sys.exit(1)
    _write_or_print_json({
        "client": result.manifest.model_dump(mode="json"),
        "event": result.event.model_dump(mode="json"),
    }, args.out)

def clients_heartbeat_command(args):
    from vad.control_plane.clients import ClientHeartbeat
    from vad.server.api.clients import ClientRegistryService
    from vad.server.db.store import ServerStore

    try:
        result = ClientRegistryService(ServerStore(Path(args.db))).heartbeat(ClientHeartbeat(
            client_id=args.client_id,
            run_id=args.run_id,
            task_id=args.task_id,
            actor=args.actor,
            role=args.role,
            summary=args.summary,
        ))
    except KeyError:
        _write_or_print_json({"error": "client_not_found", "client_id": args.client_id}, args.out)
        sys.exit(1)
    _write_or_print_json({
        "heartbeat": result.heartbeat.model_dump(mode="json"),
        "client": result.snapshot.model_dump(mode="json"),
        "event": result.event.model_dump(mode="json"),
    }, args.out)

def clients_mark_stale_command(args):
    from vad.server.api.clients import ClientRegistryService
    from vad.server.db.store import ServerStore

    stale = ClientRegistryService(ServerStore(Path(args.db))).mark_stale_clients_with_recovery(
        stale_after_seconds=args.stale_after_seconds,
        auto_reassign=args.auto_reassign,
    )
    _write_or_print_json({
        "stale_clients": [result.snapshot.model_dump(mode="json") for result in stale],
        "recovered_work_items": [
            {
                "work_item": transition.work_item.model_dump(mode="json"),
                "event": transition.event.model_dump(mode="json"),
                "decision": transition.decision.model_dump(mode="json"),
            }
            for result in stale
            for transition in result.recovered_work_items
        ],
        "reassigned_work_items": [
            {
                "work_item": decision.work_item.model_dump(mode="json") if decision.work_item else None,
                "selected_client_id": decision.selected_client_id,
                "event": decision.event.model_dump(mode="json") if decision.event else None,
                "decision": decision.decision.model_dump(mode="json"),
            }
            for result in stale
            for decision in result.reassigned_work_items
        ],
        "recovery_events": [
            event.model_dump(mode="json")
            for result in stale
            for event in result.recovery_events
        ],
    }, args.out)

def events_emit_command(args):
    import sqlite3
    from vad.control_plane.events import ControlPlaneEvent
    from vad.server.api.events import ControlPlaneEventService
    from vad.server.db.store import ServerStore

    event = ControlPlaneEvent(
        event_id=args.event_id,
        sequence=args.sequence,
        client_id=args.client_id,
        run_id=args.run_id,
        task_id=args.task_id,
        kind=args.kind,
        status=args.status,
        actor=args.actor,
        role=args.role,
        evidence_digest=args.evidence_digest,
        summary=args.summary,
    )
    try:
        result = ControlPlaneEventService(ServerStore(Path(args.db))).ingest(event.model_dump(mode="json"))
    except sqlite3.IntegrityError:
        _write_or_print_json({"error": "duplicate_control_plane_event", "event_id": event.event_id}, args.out)
        sys.exit(1)
    _write_or_print_json({
        "event": result.event.model_dump(mode="json"),
        "decision": result.decision.model_dump(mode="json"),
    }, args.out)
    if result.status_code >= 400:
        sys.exit(1)

def diff_proposals_create_command(args):
    from vad.server.api.diff_proposals import DiffProposalService
    from vad.server.db.store import ServerStore

    result = DiffProposalService(ServerStore(Path(args.db))).create({
        "run_id": args.run_id,
        "task_id": args.task_id,
        "patch_text": args.patch_text,
        "changed_files": args.changed_files,
        "summary": args.summary,
        "submitted_by": args.submitted_by,
        "role": args.role,
    })
    _write_or_print_json({
        "proposal": result.proposal.model_dump(mode="json") if result.proposal else None,
        "decision": result.decision.model_dump(mode="json"),
    }, args.out)
    if result.status_code >= 400:
        sys.exit(1)

def diff_proposals_list_command(args):
    from vad.server.api.diff_proposals import DiffProposalService
    from vad.server.db.store import ServerStore

    proposals = DiffProposalService(ServerStore(Path(args.db))).list(
        run_id=args.run_id,
        task_id=args.task_id,
    )
    _write_or_print_json({
        "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
    }, args.out)

def diff_proposals_show_command(args):
    from vad.server.api.diff_proposals import DiffProposalService
    from vad.server.db.store import ServerStore

    store = ServerStore(Path(args.db))
    try:
        proposal = DiffProposalService(store).read(args.proposal_id)
    except KeyError:
        _write_or_print_json({"error": "diff_proposal_not_found", "proposal_id": args.proposal_id}, args.out)
        sys.exit(1)
    apply_records = store.list_diff_apply_records(proposal_id=args.proposal_id)
    _write_or_print_json({
        "proposal": proposal.model_dump(mode="json"),
        "apply_records": [record.model_dump(mode="json") for record in apply_records],
    }, args.out)

def operator_intents_create_command(args):
    from pydantic import ValidationError
    from vad.control_plane.governance_records import OperatorIntentRecord
    from vad.server.db.store import ServerStore

    try:
        record = OperatorIntentRecord(
            intent_ref=args.intent_ref,
            actor=args.actor,
            role=args.role,
            scope=args.scope,
            summary=args.summary,
            granted_tools=tuple(args.granted_tools),
            live_service_opt_in=args.live_service_opt_in,
            high_risk=args.high_risk,
        )
    except ValidationError as exc:
        _write_or_print_json({"error": "invalid_operator_intent", "detail": str(exc)}, args.out)
        sys.exit(1)
    saved = ServerStore(Path(args.db)).save_operator_intent_record(record)
    _write_or_print_json({"operator_intent": saved.model_dump(mode="json")}, args.out)

def operator_intents_list_command(args):
    from vad.server.db.store import ServerStore

    records = ServerStore(Path(args.db)).list_operator_intent_records()
    _write_or_print_json({
        "operator_intents": [record.model_dump(mode="json") for record in records],
    }, args.out)

def operator_intents_show_command(args):
    from vad.server.db.store import ServerStore

    try:
        record = ServerStore(Path(args.db)).load_operator_intent_record(args.intent_ref)
    except KeyError:
        _write_or_print_json({"error": "operator_intent_not_found", "intent_ref": args.intent_ref}, args.out)
        sys.exit(1)
    _write_or_print_json({"operator_intent": record.model_dump(mode="json")}, args.out)

def _work_item_payload(result):
    return {
        "work_item": result.work_item.model_dump(mode="json") if result.work_item else None,
        "event": result.event.model_dump(mode="json") if result.event else None,
        "decision": result.decision.model_dump(mode="json"),
        **({"selected_client_id": result.selected_client_id} if hasattr(result, "selected_client_id") else {}),
    }

def work_items_create_command(args):
    import sqlite3
    from pydantic import ValidationError
    from vad.control_plane.work_items import WorkItem, WorkItemGovernance
    from vad.server.api.work_items import WorkItemService
    from vad.server.db.store import ServerStore

    try:
        governance = None
        if (
            args.effort_type
            or args.mees_estimate is not None
            or args.token_budget is not None
            or args.approval_required
            or args.live_service_opt_in
            or args.high_risk
            or args.operator_intent_ref
            or args.approval_ref
        ):
            governance = WorkItemGovernance(
                effort_type=args.effort_type or "unspecified",
                mees_estimate=0 if args.mees_estimate is None else args.mees_estimate,
                token_budget=0 if args.token_budget is None else args.token_budget,
                approval_required=args.approval_required,
                live_service_opt_in=args.live_service_opt_in,
                high_risk=args.high_risk,
                operator_intent_ref=args.operator_intent_ref,
                approval_ref=args.approval_ref,
            )
        item = WorkItem(
            work_item_id=args.work_item_id,
            run_id=args.run_id,
            title=args.title,
            description=args.description or "",
            role=args.work_role,
            requested_capability=args.requested_capability,
            priority=args.priority,
            status=args.status,
            governance=governance,
        )
        result = WorkItemService(ServerStore(Path(args.db))).create(
            item,
            actor=args.actor,
            role=args.actor_role,
            client_id=args.client_id,
            summary=args.summary,
        )
    except ValidationError as exc:
        _write_or_print_json({"error": "invalid_work_item", "detail": str(exc)}, args.out)
        sys.exit(1)
    except sqlite3.IntegrityError:
        _write_or_print_json({"error": "duplicate_work_item", "work_item_id": args.work_item_id}, args.out)
        sys.exit(1)
    _write_or_print_json(_work_item_payload(result), args.out)

def work_items_list_command(args):
    from vad.control_plane.work_items import WorkItemStatus
    from vad.server.db.store import ServerStore

    status = WorkItemStatus(args.status) if args.status else None
    items = ServerStore(Path(args.db)).list_work_items(
        run_id=args.run_id,
        status=status,
        assigned_client_id=args.assigned_client_id,
    )
    _write_or_print_json({"work_items": [item.model_dump(mode="json") for item in items]}, args.out)

def work_items_show_command(args):
    from vad.server.db.store import ServerStore

    try:
        item = ServerStore(Path(args.db)).load_work_item(args.work_item_id)
    except KeyError:
        _write_or_print_json({"error": "work_item_not_found", "work_item_id": args.work_item_id}, args.out)
        sys.exit(1)
    _write_or_print_json({"work_item": item.model_dump(mode="json")}, args.out)

def work_items_assign_command(args):
    from vad.server.api.work_items import WorkItemSchedulerService, WorkItemService
    from vad.server.db.store import ServerStore

    store = ServerStore(Path(args.db))
    try:
        if args.assigned_client_id:
            result = WorkItemService(store).assign(
                args.work_item_id,
                actor=args.actor,
                role=args.actor_role,
                client_id=args.client_id,
                assigned_client_id=args.assigned_client_id,
                lease_id=args.lease_id,
            )
        else:
            result = WorkItemSchedulerService(store).schedule_work_item(
                args.work_item_id,
                actor=args.actor,
                role=args.actor_role,
                client_id=args.client_id,
            )
    except KeyError:
        _write_or_print_json({"error": "work_item_not_found", "work_item_id": args.work_item_id}, args.out)
        sys.exit(1)
    _write_or_print_json(_work_item_payload(result), args.out)
    if result.status_code >= 400:
        sys.exit(1)

def work_items_transition_command(args):
    from vad.server.api.work_items import WorkItemService
    from vad.server.db.store import ServerStore

    service = WorkItemService(ServerStore(Path(args.db)))
    try:
        if args.work_items_command == "block":
            result = service.block(
                args.work_item_id,
                actor=args.actor,
                role=args.actor_role,
                client_id=args.client_id,
                reason=args.reason,
            )
        elif args.work_items_command == "complete":
            result = service.complete(
                args.work_item_id,
                actor=args.actor,
                role=args.actor_role,
                client_id=args.client_id,
                evidence_digest=args.evidence_digest,
            )
        elif args.work_items_command == "fail":
            result = service.fail(args.work_item_id, actor=args.actor, role=args.actor_role, client_id=args.client_id)
        elif args.work_items_command == "cancel":
            result = service.cancel(args.work_item_id, actor=args.actor, role=args.actor_role, client_id=args.client_id)
        elif args.work_items_command == "requeue":
            result = service.requeue(args.work_item_id, actor=args.actor, role=args.actor_role, client_id=args.client_id)
        else:
            raise ValueError(f"unsupported work item command {args.work_items_command}")
    except KeyError:
        _write_or_print_json({"error": "work_item_not_found", "work_item_id": args.work_item_id}, args.out)
        sys.exit(1)
    _write_or_print_json(_work_item_payload(result), args.out)
    if result.status_code >= 400:
        sys.exit(1)

def mcp_install_command(args):
    import os
    import json
    import sys
    from pathlib import Path

    client = args.client
    
    cursor_snippet = {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "vad.adapters.mcp"],
        "enabled": True
    }
    
    claude_snippet = {
        "command": sys.executable,
        "args": ["-m", "vad.adapters.mcp"]
    }

    if client in [None, "claude"]:
        config_dir = Path.home() / ".claudecode"
        config_file = config_dir / "config.json"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            config_data = {}
            if config_file.exists():
                with open(config_file, "r") as f:
                    config_data = json.load(f)
            if "mcpServers" not in config_data:
                config_data["mcpServers"] = {}
            config_data["mcpServers"]["vad"] = claude_snippet
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            print(f"[SUCCESS] Configured Claude Code CLI extension at {config_file}")
        except Exception as e:
            print(f"[WARNING] Could not automatically configure Claude Code: {e}")
            
    if client in [None, "cursor"]:
        if sys.platform == "win32":
            appdata = Path(os.environ.get("APPDATA", "~/AppData/Roaming")).expanduser()
            cursor_dir = appdata / "Cursor"
        elif sys.platform == "darwin":
            cursor_dir = Path.home() / "Library" / "Application Support" / "Cursor"
        else:
            cursor_dir = Path.home() / ".config" / "Cursor"
            
        config_file = cursor_dir / "User" / "globalStorage" / "meta.mcp" / "config.json"
        try:
            config_file.parent.mkdir(parents=True, exist_ok=True)
            config_data = {}
            if config_file.exists():
                with open(config_file, "r") as f:
                    config_data = json.load(f)
            if "mcpServers" not in config_data:
                config_data["mcpServers"] = {}
            config_data["mcpServers"]["vad"] = cursor_snippet
            with open(config_file, "w") as f:
                json.dump(config_data, f, indent=2)
            print(f"[SUCCESS] Configured Cursor extension at {config_file}")
        except Exception as e:
            print(f"[WARNING] Could not automatically configure Cursor: {e}")

    print("\n" + "="*50)
    print("VAD MCP SERVER MANUAL CONFIGURATION GUIDE")
    print("="*50)
    print("If automatic configuration failed, or you are using another LLM CLI,")
    print("use the following details to register the VAD MCP Server:\n")
    print(f"Executable (Python): {sys.executable}")
    print(f"Arguments: -m vad.adapters.mcp\n")
    print("--- Claude Code (~/.claudecode/config.json) snippet ---")
    print(json.dumps({"mcpServers": {"vad": claude_snippet}}, indent=2))
    print("\n--- Cursor (~/.../User/globalStorage/meta.mcp/config.json) snippet ---")
    print(json.dumps({"mcpServers": {"vad": cursor_snippet}}, indent=2))


def plugins_install_command(args):
    from vad.control_plane.plugins import VADPluginManifest, create_plugin_installer_dry_run

    if not args.dry_run:
        print("Plugin install currently supports --dry-run only.", file=sys.stderr)
        sys.exit(1)
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"File {args.manifest} not found.", file=sys.stderr)
        sys.exit(1)
    try:
        manifest = VADPluginManifest(**load_structured_file(manifest_path))
        dry_run = create_plugin_installer_dry_run(
            manifest,
            workspace_root=Path(args.workspace_root),
            user_config_root=Path(args.user_config_root),
        )
        _write_or_print_json(dry_run.model_dump(mode="json"), args.out)
    except (ValidationError, ValueError) as e:
        print(f"Plugin install dry-run failed: {e}", file=sys.stderr)
        sys.exit(1)
    print("="*50 + "\n")

def main():
    parser = argparse.ArgumentParser(prog="vad")
    subparsers = parser.add_subparsers(dest="command")
    
    # EIP commands
    eip_parser = subparsers.add_parser("eip")
    eip_subparsers = eip_parser.add_subparsers(dest="eip_command")
    
    init_parser = eip_subparsers.add_parser("init")
    init_parser.add_argument("--name", required=True, help="EIP name")
    init_parser.add_argument("--out", default="eip.yaml", help="Path to write the EIP template")
    init_parser.add_argument("--from-assessment", help="Path to JSON assessment from vad ask assess")
    init_parser.add_argument("--force", action="store_true", help="Overwrite --out if it exists")
    
    validate_parser = eip_subparsers.add_parser("validate")
    validate_parser.add_argument("file", help="Path to EIP file")

    normalize_parser = eip_subparsers.add_parser("normalize")
    normalize_parser.add_argument("file", help="Path to EIP file")
    normalize_parser.add_argument("--out", help="Path to write normalized EIP")
    normalize_parser.add_argument("--json", action="store_true", help="Emit JSON instead of YAML")
    normalize_parser.add_argument("--force", action="store_true", help="Overwrite --out if it exists")
    
    diff_parser = eip_subparsers.add_parser("diff")
    diff_parser.add_argument("old_file", help="Path to old EIP file")
    diff_parser.add_argument("new_file", help="Path to new EIP file")
    diff_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    
    retro_parser = eip_subparsers.add_parser("retro")
    retro_parser.add_argument("file", help="Path to evidence bundle file")

    ask_parser = subparsers.add_parser("ask")
    ask_subparsers = ask_parser.add_subparsers(dest="ask_command")
    assess_parser = ask_subparsers.add_parser("assess")
    assess_parser.add_argument("file", help="Path to ask text file")
    assess_parser.add_argument("--out", help="Path to write JSON assessment")
    assess_parser.add_argument("--json", action="store_true", help="Print JSON assessment")

    proof_parser = subparsers.add_parser("proof")
    proof_subparsers = proof_parser.add_subparsers(dest="proof_command")
    proof_map_parser = proof_subparsers.add_parser("map")
    proof_map_parser.add_argument("file", help="Path to EIP file")
    proof_map_parser.add_argument("--out", help="Path to write proof plan")
    proof_map_parser.add_argument("--force", action="store_true", help="Overwrite --out if it exists")

    loop_parser = subparsers.add_parser("loop")
    loop_subparsers = loop_parser.add_subparsers(dest="loop_command")
    loop_run_parser = loop_subparsers.add_parser("run")
    loop_run_parser.add_argument("eip_file", help="Path to EIP file")
    loop_run_parser.add_argument("proof_plan_file", help="Path to proof plan file")
    loop_run_parser.add_argument("--builder", default="builder", help="Builder identity")
    loop_run_parser.add_argument("--verifier", default="verifier", help="Verifier identity")
    loop_run_parser.add_argument("--out", help="Path to write run evidence")

    evidence_parser = subparsers.add_parser("evidence")
    evidence_subparsers = evidence_parser.add_subparsers(dest="evidence_command")
    evidence_inspect_parser = evidence_subparsers.add_parser("inspect")
    evidence_inspect_parser.add_argument("file", help="Path to evidence file")

    effort_parser = subparsers.add_parser("effort")
    effort_subparsers = effort_parser.add_subparsers(dest="effort_command")
    effort_score_parser = effort_subparsers.add_parser("score")
    effort_score_parser.add_argument("--before", default="HEAD", help="Git base revision")
    effort_score_parser.add_argument("--after", default="WORKTREE", help="Git target revision or WORKTREE")
    effort_score_parser.add_argument("--type", required=True, help="Effort type")
    effort_score_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    effort_score_parser.add_argument("--readable", action="store_true", help="Emit readable summary")
    effort_score_parser.add_argument("--warn-only", action="store_true", help="Do not exit nonzero for warn/block MEES policy")

    repo_parser = subparsers.add_parser("repo")
    repo_subparsers = repo_parser.add_subparsers(dest="repo_command")
    repo_assess_parser = repo_subparsers.add_parser("assess")
    repo_assess_parser.add_argument("path", help="Path to repository")
    repo_patch_parser = repo_subparsers.add_parser("patch")
    repo_patch_parser.add_argument("path", help="Path to repository")
    repo_patch_parser.add_argument("eip_file", help="Path to EIP file")
    repo_patch_parser.add_argument("proof_plan_file", help="Path to proof plan file")
    repo_patch_parser.add_argument("--patch", required=True, help="Path to unified diff patch file")
    repo_patch_parser.add_argument("--out", help="Path to write patch evidence JSON")
    repo_patch_parser.add_argument("--allow-dirty", action="store_true", help="Allow patching a dirty repository")
    repo_patch_parser.add_argument("--approve-dependencies", action="store_true", help="Approve dependency manifest changes")
    repo_run_parser = repo_subparsers.add_parser("run")
    repo_run_parser.add_argument("path", help="Path to repository")
    repo_run_parser.add_argument("ask_file", help="Path to ask text file")
    repo_run_parser.add_argument("--patch", required=True, help="Path to unified diff patch file")
    repo_run_parser.add_argument("--scope", action="append", help="Allowed EIP scope boundary; defaults to patch top-level paths")
    repo_run_parser.add_argument("--out", help="Path to write run evidence JSON")
    repo_run_parser.add_argument("--allow-dirty", action="store_true", help="Allow patching a dirty repository")
    repo_run_parser.add_argument("--approve-dependencies", action="store_true", help="Approve dependency manifest changes")

    sign_parser = subparsers.add_parser("sign")
    sign_subparsers = sign_parser.add_subparsers(dest="sign_command")
    sign_evidence_parser = sign_subparsers.add_parser("evidence")
    sign_evidence_parser.add_argument("evidence_file", help="Path to evidence JSON/YAML file")
    sign_evidence_parser.add_argument("--key-id", required=True, help="Signing key identifier")
    sign_evidence_parser.add_argument("--secret-file", required=True, help="Path to local development signing secret")
    sign_evidence_parser.add_argument("--out", help="Path to write signed evidence JSON")
    sign_verify_parser = sign_subparsers.add_parser("verify")
    sign_verify_parser.add_argument("signed_file", help="Path to signed evidence JSON/YAML file")
    sign_verify_parser.add_argument("--secret-file", required=True, help="Path to local development signing secret")
    sign_verify_parser.add_argument("--out", help="Path to write verification JSON")

    swarm_parser = subparsers.add_parser("swarm")
    swarm_subparsers = swarm_parser.add_subparsers(dest="swarm_command")
    swarm_run_parser = swarm_subparsers.add_parser("run")
    swarm_run_parser.add_argument("--run-id", default="local-swarm-run", help="Run identifier")
    swarm_run_parser.add_argument("--state", help="Path to persist swarm state JSON")
    swarm_run_parser.add_argument("--out", help="Path to write swarm run JSON")
    swarm_run_parser.add_argument("--fixture", help="Path to a local demonstrator fixture")
    swarm_run_parser.add_argument("--workdir", help="Path for a copied fixture repo workspace")
    swarm_status_parser = swarm_subparsers.add_parser("status")
    swarm_status_parser.add_argument("state", help="Path to persisted swarm state JSON")
    swarm_status_parser.add_argument("--out", help="Path to write swarm status JSON")

    deploy_parser = subparsers.add_parser("deploy")
    deploy_subparsers = deploy_parser.add_subparsers(dest="deploy_command")
    deploy_plan_parser = deploy_subparsers.add_parser("plan")
    deploy_plan_parser.add_argument("eip_file", help="Path to EIP file")
    deploy_plan_parser.add_argument("target_file", help="Path to deployment target JSON/YAML file")
    deploy_plan_parser.add_argument("--plan-id", help="Deployment plan id")
    deploy_plan_parser.add_argument("--approval-ref", help="Approval evidence reference")
    deploy_plan_parser.add_argument("--evidence-ref", help="Run evidence reference")
    deploy_plan_parser.add_argument("--out", help="Path to write deployment plan YAML")
    deploy_dry_run_parser = deploy_subparsers.add_parser("dry-run")
    deploy_dry_run_parser.add_argument("deployment_plan", help="Path to deployment plan JSON/YAML file")
    deploy_dry_run_parser.add_argument("--out", help="Path to write dry-run JSON")
    deploy_apply_parser = deploy_subparsers.add_parser("apply")
    deploy_apply_parser.add_argument("deployment_plan", help="Path to deployment plan JSON/YAML file")
    deploy_apply_parser.add_argument("--approval-ref", help="Approval evidence reference")
    deploy_apply_parser.add_argument("--out", help="Path to write deployment apply JSON")
    deploy_rollback_parser = deploy_subparsers.add_parser("rollback")
    deploy_rollback_parser.add_argument("deployment_record", help="Path to deployment apply JSON/YAML record")
    deploy_rollback_parser.add_argument("--rollback-approval-ref", required=True, help="Rollback approval evidence reference")
    deploy_rollback_parser.add_argument("--out", help="Path to write rollback JSON")
    deploy_demo_parser = deploy_subparsers.add_parser("demo", help="Run the signed local deployment demonstrator")
    deploy_demo_parser.add_argument("--fixture", default="examples/level3-demo", help="Path to the Level 3 demo fixture")
    deploy_demo_parser.add_argument("--out-dir", required=True, help="Directory for deployment demo artifacts")
    deploy_demo_parser.add_argument("--key-id", default="level3-demo-deploy", help="Local development signing key id")
    deploy_demo_parser.add_argument("--out", help="Path to write deployment demo JSON")
    deploy_failure_demo_parser = deploy_subparsers.add_parser("failure-demo", help="Run the failed rollout and rollback demonstrator")
    deploy_failure_demo_parser.add_argument("--fixture", default="examples/level3-demo", help="Path to the Level 3 demo fixture")
    deploy_failure_demo_parser.add_argument("--out-dir", required=True, help="Directory for failure demo artifacts")
    deploy_failure_demo_parser.add_argument("--db", help="SQLite dashboard database path")
    deploy_failure_demo_parser.add_argument("--out", help="Path to write failure demo JSON")
    
    # MCP commands
    mcp_parser = subparsers.add_parser("mcp")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command")
    
    mcp_subparsers.add_parser("run", help="Run the MCP stdio server")
    
    install_parser = mcp_subparsers.add_parser("install", help="Install/configure VAD CLI extension")
    install_parser.add_argument("client", nargs="?", choices=["claude", "cursor"], help="Target client for automatic installation")

    # Plugin installer commands
    plugins_parser = subparsers.add_parser("plugins")
    plugins_subparsers = plugins_parser.add_subparsers(dest="plugins_command")
    plugins_install_parser = plugins_subparsers.add_parser("install", help="Preview plugin installation changes")
    plugins_install_parser.add_argument("manifest", help="Path to VAD plugin manifest JSON/YAML")
    plugins_install_parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing files")
    plugins_install_parser.add_argument("--workspace-root", default=".", help="Workspace root for workspace-scoped config")
    plugins_install_parser.add_argument("--user-config-root", default=".vad/user-config", help="User config root for user-scoped config")
    plugins_install_parser.add_argument("--out", help="Path to write dry-run JSON")

    # UI commands
    ui_parser = subparsers.add_parser("ui")
    ui_subparsers = ui_parser.add_subparsers(dest="ui_command")
    ui_serve_parser = ui_subparsers.add_parser("serve", help="Serve the local VAD UI/API dashboard")
    ui_serve_parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    ui_serve_parser.add_argument("--port", type=int, default=8080, help="Port to bind")
    ui_serve_parser.add_argument("--evidence-root", default=".vad/ui/evidence", help="Evidence file directory")
    ui_serve_parser.add_argument("--db", default=".vad/ui/vad.sqlite3", help="SQLite database path")
    ui_serve_parser.add_argument("--ui-root", default=".vad/ui/build", help="Built UI directory")
    ui_serve_parser.add_argument("--seed-demo", action="store_true", help="Seed deterministic local demo data")
    ui_serve_parser.add_argument("--seed-level3-demo", action="store_true", help="Seed deterministic Level 3 demonstrator data")
    ui_serve_parser.add_argument("--seed-multi-client-simulator", action="store_true", help="Seed deterministic Level 4 multi-client simulator data")

    # Control-plane commands
    control_plane_parser = subparsers.add_parser("control-plane")
    control_plane_subparsers = control_plane_parser.add_subparsers(dest="control_plane_command")
    control_plane_serve_parser = control_plane_subparsers.add_parser("serve", help="Serve the local VAD Level 4 control plane")
    control_plane_serve_parser.add_argument("--host", help="Host interface to bind")
    control_plane_serve_parser.add_argument("--port", type=int, help="Port to bind")
    control_plane_serve_parser.add_argument("--config", help="Control-plane config path")
    control_plane_serve_parser.add_argument("--db", help="SQLite database path")
    control_plane_serve_parser.add_argument("--evidence-root", help="Evidence file directory")
    control_plane_serve_parser.add_argument("--ui-root", help="Built UI directory")
    control_plane_serve_parser.add_argument("--plugin-root", help="Plugin directory")
    control_plane_serve_parser.add_argument("--log-level", help="Log level")
    control_plane_serve_parser.add_argument("--allow-non-local-bind", action="store_true", help="Allow binding outside localhost")
    control_plane_serve_parser.add_argument("--seed-demo", action="store_true", help="Seed deterministic local demo data")
    control_plane_serve_parser.add_argument("--seed-level3-demo", action="store_true", help="Seed deterministic Level 3 demonstrator data")
    control_plane_serve_parser.add_argument("--seed-multi-client-simulator", action="store_true", help="Seed deterministic Level 4 multi-client simulator data")
    control_plane_serve_parser.add_argument("--test-start", action="store_true", help="Start, mark ready, then stop for deterministic tests")

    # Local OS demo command
    local_os_parser = subparsers.add_parser("local-os")
    local_os_subparsers = local_os_parser.add_subparsers(dest="local_os_command")
    local_os_demo_parser = local_os_subparsers.add_parser("demo", help="Start the local Level 4 VAD OS demo")
    local_os_demo_parser.add_argument("--host", default="127.0.0.1", help="Local host interface to bind")
    local_os_demo_parser.add_argument("--port", type=int, default=8080, help="Port to bind")
    local_os_demo_parser.add_argument("--db", default=".vad/local-os/vad.sqlite3", help="SQLite database path")
    local_os_demo_parser.add_argument("--evidence-root", default=".vad/local-os/evidence", help="Evidence file directory")
    local_os_demo_parser.add_argument("--ui-root", default=".vad/local-os/ui", help="Built UI directory")
    local_os_demo_parser.add_argument("--plugin-root", default=".vad/local-os/plugins", help="Plugin directory")
    local_os_demo_parser.add_argument("--allow-non-local-bind", action="store_true", help="Allow binding outside localhost")
    local_os_demo_parser.add_argument("--test-start", action="store_true", help="Start, mark ready, then stop for deterministic tests")

    # Client registry commands
    clients_parser = subparsers.add_parser("clients")
    clients_subparsers = clients_parser.add_subparsers(dest="clients_command")
    clients_register_parser = clients_subparsers.add_parser("register", help="Register a local coding client")
    clients_register_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    clients_register_parser.add_argument("--client-id", required=True, help="Safe client identifier")
    clients_register_parser.add_argument("--display-name", required=True, help="Human-readable client name")
    clients_register_parser.add_argument("--client-type", required=True, choices=["codex", "claude_code", "vscode", "antigravity", "windsurf", "cursor", "opencode", "generic_mcp", "other"])
    clients_register_parser.add_argument("--version", required=True, help="Client version")
    clients_register_parser.add_argument("--connection-mode", required=True, choices=["mcp", "plugin", "sdk", "cli", "http"])
    clients_register_parser.add_argument("--capability", action="append", required=True, help="Declared client capability; repeat for multiple capabilities")
    clients_register_parser.add_argument("--workspace-root", required=True, help="Local workspace root")
    clients_register_parser.add_argument("--trust-state", default="untrusted", choices=["untrusted", "trusted", "quarantined"], help="Local trust state")
    clients_register_parser.add_argument("--out", help="Path to write JSON result")
    clients_list_parser = clients_subparsers.add_parser("list", help="List registered local coding clients")
    clients_list_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    clients_list_parser.add_argument("--out", help="Path to write JSON result")
    clients_heartbeat_parser = clients_subparsers.add_parser("heartbeat", help="Record a local client heartbeat")
    clients_heartbeat_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    clients_heartbeat_parser.add_argument("client_id", help="Client identifier")
    clients_heartbeat_parser.add_argument("--run-id", help="Current run identifier")
    clients_heartbeat_parser.add_argument("--task-id", help="Current task identifier")
    clients_heartbeat_parser.add_argument("--actor", required=True, help="Client actor identity")
    clients_heartbeat_parser.add_argument("--role", required=True, help="Current VAD role")
    clients_heartbeat_parser.add_argument("--summary", default="Client heartbeat.", help="Heartbeat summary")
    clients_heartbeat_parser.add_argument("--out", help="Path to write JSON result")
    clients_mark_stale_parser = clients_subparsers.add_parser("mark-stale", help="Mark stale clients based on last heartbeat")
    clients_mark_stale_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    clients_mark_stale_parser.add_argument("--stale-after-seconds", type=int, default=120, help="Heartbeat age that marks a client stale")
    clients_mark_stale_parser.add_argument("--auto-reassign", action="store_true", help="Reassign requeued work to another active trusted client")
    clients_mark_stale_parser.add_argument("--out", help="Path to write JSON result")
    clients_unregister_parser = clients_subparsers.add_parser("unregister", help="Unregister a local coding client")
    clients_unregister_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    clients_unregister_parser.add_argument("client_id", help="Client identifier to remove")
    clients_unregister_parser.add_argument("--out", help="Path to write JSON result")

    # Work item commands
    work_items_parser = subparsers.add_parser("work-items")
    work_items_subparsers = work_items_parser.add_subparsers(dest="work_items_command")
    work_item_statuses = [
        "planned",
        "queued",
        "assigned",
        "running",
        "blocked",
        "waiting_for_human",
        "verifying",
        "approved",
        "completed",
        "failed",
        "cancelled",
        "requeued",
    ]
    work_items_create_parser = work_items_subparsers.add_parser("create", help="Create a durable orchestrator work item")
    work_items_create_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    work_items_create_parser.add_argument("--work-item-id", required=True, help="Safe work item identifier")
    work_items_create_parser.add_argument("--run-id", required=True, help="Run identifier")
    work_items_create_parser.add_argument("--title", required=True, help="Work item title")
    work_items_create_parser.add_argument("--description", help="Work item description")
    work_items_create_parser.add_argument("--work-role", required=True, help="Required role for the work")
    work_items_create_parser.add_argument("--requested-capability", help="Optional requested client capability")
    work_items_create_parser.add_argument("--priority", type=int, default=100, help="Lower values are scheduled first")
    work_items_create_parser.add_argument("--status", default="queued", choices=work_item_statuses, help="Initial work item status")
    work_items_create_parser.add_argument("--effort-type", help="Governance effort type for this work item")
    work_items_create_parser.add_argument("--mees-estimate", type=int, help="Governance MEES estimate for this work item")
    work_items_create_parser.add_argument("--token-budget", type=int, help="Governance token budget for this work item")
    work_items_create_parser.add_argument("--approval-required", action="store_true", help="Mark the work item as requiring approval")
    work_items_create_parser.add_argument("--live-service-opt-in", action="store_true", help="Record explicit live-service opt-in for this work item")
    work_items_create_parser.add_argument("--high-risk", action="store_true", help="Record high-risk governance state for this work item")
    work_items_create_parser.add_argument("--operator-intent-ref", help="Current operator intent evidence reference")
    work_items_create_parser.add_argument("--approval-ref", help="Approval evidence reference")
    work_items_create_parser.add_argument("--actor", default="operator", help="Actor creating the work item")
    work_items_create_parser.add_argument("--actor-role", default="operator", help="Actor role creating the work item")
    work_items_create_parser.add_argument("--client-id", default="control-plane", help="Client id for emitted evidence")
    work_items_create_parser.add_argument("--summary", help="Optional event summary")
    work_items_create_parser.add_argument("--out", help="Path to write JSON result")
    work_items_list_parser = work_items_subparsers.add_parser("list", help="List durable orchestrator work items")
    work_items_list_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    work_items_list_parser.add_argument("--run-id", help="Filter by run id")
    work_items_list_parser.add_argument("--status", choices=work_item_statuses, help="Filter by work item status")
    work_items_list_parser.add_argument("--assigned-client-id", help="Filter by assigned client")
    work_items_list_parser.add_argument("--out", help="Path to write JSON result")
    work_items_show_parser = work_items_subparsers.add_parser("show", help="Show one durable orchestrator work item")
    work_items_show_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    work_items_show_parser.add_argument("work_item_id", help="Work item identifier")
    work_items_show_parser.add_argument("--out", help="Path to write JSON result")
    work_items_assign_parser = work_items_subparsers.add_parser("assign", help="Assign a work item directly or through the scheduler")
    work_items_assign_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    work_items_assign_parser.add_argument("work_item_id", help="Work item identifier")
    work_items_assign_parser.add_argument("--assigned-client-id", help="Explicit client assignment; omit to use scheduler")
    work_items_assign_parser.add_argument("--lease-id", help="Optional task lease id")
    work_items_assign_parser.add_argument("--actor", default="operator", help="Actor assigning the work item")
    work_items_assign_parser.add_argument("--actor-role", default="operator", help="Actor role assigning the work item")
    work_items_assign_parser.add_argument("--client-id", default="control-plane", help="Client id for emitted evidence")
    work_items_assign_parser.add_argument("--out", help="Path to write JSON result")
    for action in ["block", "complete", "fail", "cancel", "requeue"]:
        action_parser = work_items_subparsers.add_parser(action, help=f"{action} a durable orchestrator work item")
        action_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
        action_parser.add_argument("work_item_id", help="Work item identifier")
        action_parser.add_argument("--actor", default="operator", help="Actor changing the work item")
        action_parser.add_argument("--actor-role", default="operator", help="Actor role changing the work item")
        action_parser.add_argument("--client-id", default="control-plane", help="Client id for emitted evidence")
        action_parser.add_argument("--out", help="Path to write JSON result")
        if action == "block":
            action_parser.add_argument("--reason", required=True, help="Blocked reason")
        if action == "complete":
            action_parser.add_argument("--evidence-digest", help="Optional 64-character evidence digest")

    # Control-plane event commands
    events_parser = subparsers.add_parser("events")
    events_subparsers = events_parser.add_subparsers(dest="events_command")
    events_emit_parser = events_subparsers.add_parser("emit", help="Emit a local control-plane event")
    events_emit_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    events_emit_parser.add_argument("--event-id", required=True, help="Event identifier")
    events_emit_parser.add_argument("--sequence", type=int, required=True, help="Monotonic event sequence")
    events_emit_parser.add_argument("--client-id", required=True, help="Client identifier")
    events_emit_parser.add_argument("--run-id", help="Run identifier")
    events_emit_parser.add_argument("--task-id", help="Task identifier")
    events_emit_parser.add_argument("--kind", required=True, choices=[
        "heartbeat",
        "tool_call_started",
        "tool_call_finished",
        "file_change_proposed",
        "file_change_applied",
        "proof_started",
        "proof_finished",
        "policy_denied",
        "approval_requested",
        "approval_recorded",
        "signer_event",
        "deployment_event",
        "message",
        "blocker",
        "recovery_action",
        "swarm",
        "provider",
        "signing",
        "deployment",
        "work_item",
        "task_lease",
    ])
    events_emit_parser.add_argument("--status", required=True, choices=["active", "passed", "failed", "blocked", "needs_human", "stale"])
    events_emit_parser.add_argument("--actor", required=True, help="Actor identity")
    events_emit_parser.add_argument("--role", required=True, help="Actor role")
    events_emit_parser.add_argument("--evidence-digest", help="Optional 64-character evidence digest")
    events_emit_parser.add_argument("--summary", required=True, help="Event summary")
    events_emit_parser.add_argument("--out", help="Path to write JSON result")

    diff_proposals_parser = subparsers.add_parser("diff-proposals")
    diff_proposals_subparsers = diff_proposals_parser.add_subparsers(dest="diff_proposals_command")
    diff_proposals_create_parser = diff_proposals_subparsers.add_parser("create", help="Persist a diff proposal before apply")
    diff_proposals_create_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    diff_proposals_create_parser.add_argument("--run-id", required=True, help="Run identifier")
    diff_proposals_create_parser.add_argument("--task-id", required=True, help="Task identifier")
    diff_proposals_create_parser.add_argument("--patch-text", required=True, help="Unified diff text")
    diff_proposals_create_parser.add_argument("--changed-file", action="append", required=True, dest="changed_files", help="Changed file path")
    diff_proposals_create_parser.add_argument("--summary", required=True, help="Proposal summary")
    diff_proposals_create_parser.add_argument("--submitted-by", default="builder", help="Submitting actor")
    diff_proposals_create_parser.add_argument("--role", default="builder", help="Submitting role")
    diff_proposals_create_parser.add_argument("--out", help="Path to write JSON result")
    diff_proposals_list_parser = diff_proposals_subparsers.add_parser("list", help="List persisted diff proposals")
    diff_proposals_list_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    diff_proposals_list_parser.add_argument("--run-id", help="Filter by run id")
    diff_proposals_list_parser.add_argument("--task-id", help="Filter by task id")
    diff_proposals_list_parser.add_argument("--out", help="Path to write JSON result")
    diff_proposals_show_parser = diff_proposals_subparsers.add_parser("show", help="Show one persisted diff proposal")
    diff_proposals_show_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    diff_proposals_show_parser.add_argument("proposal_id", help="Diff proposal identifier")
    diff_proposals_show_parser.add_argument("--out", help="Path to write JSON result")

    operator_intents_parser = subparsers.add_parser("operator-intents")
    operator_intents_subparsers = operator_intents_parser.add_subparsers(dest="operator_intents_command")
    operator_intents_create_parser = operator_intents_subparsers.add_parser("create", help="Record durable operator intent")
    operator_intents_create_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    operator_intents_create_parser.add_argument("--intent-ref", required=True, help="Stable operator intent reference")
    operator_intents_create_parser.add_argument("--actor", required=True, help="Operator actor")
    operator_intents_create_parser.add_argument("--role", required=True, help="Operator role")
    operator_intents_create_parser.add_argument("--scope", required=True, help="Intent scope")
    operator_intents_create_parser.add_argument("--summary", required=True, help="Intent summary")
    operator_intents_create_parser.add_argument("--granted-tool", action="append", default=[], dest="granted_tools", help="Granted tool name")
    operator_intents_create_parser.add_argument("--live-service-opt-in", action="store_true", help="Record live-service opt-in")
    operator_intents_create_parser.add_argument("--high-risk", action="store_true", help="Record high-risk intent")
    operator_intents_create_parser.add_argument("--out", help="Path to write JSON result")
    operator_intents_list_parser = operator_intents_subparsers.add_parser("list", help="List durable operator intent records")
    operator_intents_list_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    operator_intents_list_parser.add_argument("--out", help="Path to write JSON result")
    operator_intents_show_parser = operator_intents_subparsers.add_parser("show", help="Show one operator intent record")
    operator_intents_show_parser.add_argument("--db", default=".vad/control-plane/vad.sqlite3", help="SQLite database path")
    operator_intents_show_parser.add_argument("intent_ref", help="Operator intent reference")
    operator_intents_show_parser.add_argument("--out", help="Path to write JSON result")

    args = parser.parse_args()
    
    if args.command == "eip":
        if args.eip_command == "init":
            init_command(args)
        elif args.eip_command == "validate":
            validate_command(args)
        elif args.eip_command == "normalize":
            normalize_command(args)
        elif args.eip_command == "diff":
            diff_command(args)
        elif args.eip_command == "retro":
            retro_command(args)
        else:
            eip_parser.print_help()
    elif args.command == "mcp":
        if args.mcp_command == "run":
            mcp_run_command(args)
        elif args.mcp_command == "install":
            mcp_install_command(args)
        else:
            mcp_parser.print_help()
    elif args.command == "ui":
        if args.ui_command == "serve":
            ui_serve_command(args)
        else:
            ui_parser.print_help()
    elif args.command == "control-plane":
        if args.control_plane_command == "serve":
            control_plane_serve_command(args)
        else:
            control_plane_parser.print_help()
    elif args.command == "local-os":
        if args.local_os_command == "demo":
            local_os_demo_command(args)
        else:
            local_os_parser.print_help()
    elif args.command == "clients":
        if args.clients_command == "register":
            clients_register_command(args)
        elif args.clients_command == "list":
            clients_list_command(args)
        elif args.clients_command == "heartbeat":
            clients_heartbeat_command(args)
        elif args.clients_command == "mark-stale":
            clients_mark_stale_command(args)
        elif args.clients_command == "unregister":
            clients_unregister_command(args)
        else:
            clients_parser.print_help()
    elif args.command == "work-items":
        if args.work_items_command == "create":
            work_items_create_command(args)
        elif args.work_items_command == "list":
            work_items_list_command(args)
        elif args.work_items_command == "show":
            work_items_show_command(args)
        elif args.work_items_command == "assign":
            work_items_assign_command(args)
        elif args.work_items_command in {"block", "complete", "fail", "cancel", "requeue"}:
            work_items_transition_command(args)
        else:
            work_items_parser.print_help()
    elif args.command == "events":
        if args.events_command == "emit":
            events_emit_command(args)
        else:
            events_parser.print_help()
    elif args.command == "diff-proposals":
        if args.diff_proposals_command == "create":
            diff_proposals_create_command(args)
        elif args.diff_proposals_command == "list":
            diff_proposals_list_command(args)
        elif args.diff_proposals_command == "show":
            diff_proposals_show_command(args)
        else:
            diff_proposals_parser.print_help()
    elif args.command == "operator-intents":
        if args.operator_intents_command == "create":
            operator_intents_create_command(args)
        elif args.operator_intents_command == "list":
            operator_intents_list_command(args)
        elif args.operator_intents_command == "show":
            operator_intents_show_command(args)
        else:
            operator_intents_parser.print_help()
    elif args.command == "plugins":
        if args.plugins_command == "install":
            plugins_install_command(args)
        else:
            plugins_parser.print_help()
    elif args.command == "ask":
        if args.ask_command == "assess":
            ask_assess_command(args)
        else:
            ask_parser.print_help()
    elif args.command == "proof":
        if args.proof_command == "map":
            proof_map_command(args)
        else:
            proof_parser.print_help()
    elif args.command == "loop":
        if args.loop_command == "run":
            loop_run_command(args)
        else:
            loop_parser.print_help()
    elif args.command == "evidence":
        if args.evidence_command == "inspect":
            evidence_inspect_command(args)
        else:
            evidence_parser.print_help()
    elif args.command == "effort":
        if args.effort_command == "score":
            effort_score_command(args)
        else:
            effort_parser.print_help()
    elif args.command == "repo":
        if args.repo_command == "assess":
            repo_assess_command(args)
        elif args.repo_command == "patch":
            repo_patch_command(args)
        elif args.repo_command == "run":
            repo_run_command(args)
        else:
            repo_parser.print_help()
    elif args.command == "sign":
        if args.sign_command == "evidence":
            sign_evidence_command(args)
        elif args.sign_command == "verify":
            sign_verify_command(args)
        else:
            sign_parser.print_help()
    elif args.command == "swarm":
        if args.swarm_command == "run":
            swarm_run_command(args)
        elif args.swarm_command == "status":
            swarm_status_command(args)
        else:
            swarm_parser.print_help()
    elif args.command == "deploy":
        if args.deploy_command == "plan":
            deploy_plan_command(args)
        elif args.deploy_command == "dry-run":
            deploy_dry_run_command(args)
        elif args.deploy_command == "apply":
            deploy_apply_command(args)
        elif args.deploy_command == "rollback":
            deploy_rollback_command(args)
        elif args.deploy_command == "demo":
            deploy_demo_command(args)
        elif args.deploy_command == "failure-demo":
            deploy_failure_demo_command(args)
        else:
            deploy_parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
