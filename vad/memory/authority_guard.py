from __future__ import annotations

import re
from typing import Any

from vad.memory.contracts import AdvisoryContext, InferredLesson, ObservedFact

_SHA256_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")

FORBIDDEN_AUTHORITY_FIELDS: set[str] = {
    "approval_granted",
    "verification_override",
    "policy_override",
    "evidence_ledger_mutation",
    "release_approved",
    "bypass_gate",
}

AUTHORITATIVE_STATE_TARGETS: tuple[str, ...] = (
    "evidence_ledger",
    "verification_result",
    "approval_record",
    "policy_decision",
)

_READ_ONLY_PREFIXES: tuple[str, ...] = (
    "read_",
    "read:",
    "get_",
    "get:",
    "list_",
    "query_",
    "view_",
)

_MUTATION_KEYWORDS: tuple[str, ...] = (
    "mutate",
    "write",
    "update",
    "delete",
    "insert",
    "alter",
    "override",
    "grant",
    "approve",
    "modify",
    "set",
    "create",
    "append",
    "drop",
)


class MemoryAuthorityViolationError(PermissionError):
    """Raised when a memory payload or operation violates VAD authority boundaries."""


def _find_forbidden_fields(value: Any, path: str = "metadata") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for raw_key, nested_val in value.items():
            key_str = str(raw_key)
            key_norm = key_str.strip().lower()
            if key_norm in FORBIDDEN_AUTHORITY_FIELDS:
                findings.append(f"{path}.{key_str}")
            findings.extend(_find_forbidden_fields(nested_val, f"{path}.{key_str}"))
    elif isinstance(value, (list, tuple)):
        for idx, item in enumerate(value):
            findings.extend(_find_forbidden_fields(item, f"{path}[{idx}]"))
    return findings


def _assert_no_forbidden_metadata(obj: Any, label: str) -> None:
    metadata = getattr(obj, "metadata", None)
    if metadata is not None:
        violations = _find_forbidden_fields(metadata)
        if violations:
            raise MemoryAuthorityViolationError(
                f"Forbidden authority-override metadata on {label}: {', '.join(violations)}"
            )
    extra = getattr(obj, "model_extra", None)
    if extra:
        violations = _find_forbidden_fields(extra, path="model_extra")
        if violations:
            raise MemoryAuthorityViolationError(
                f"Forbidden authority-override extra fields on {label}: {', '.join(violations)}"
            )


class MemoryAuthorityGuard:
    """Enforces strict authority boundaries between VAD and memory providers."""

    def validate_outbound_fact(self, fact: ObservedFact) -> ObservedFact:
        if fact.authoritative_source != "vad":
            raise MemoryAuthorityViolationError(
                f"ObservedFact {fact.fact_id!r} must have authoritative_source='vad', "
                f"got {fact.authoritative_source!r}"
            )
        if not isinstance(fact.source_evidence_digest, str) or not _SHA256_HEX_RE.fullmatch(
            fact.source_evidence_digest
        ):
            raise MemoryAuthorityViolationError(
                f"ObservedFact {fact.fact_id!r} has invalid 64-hex source_evidence_digest"
            )
        _assert_no_forbidden_metadata(fact, f"ObservedFact({fact.fact_id})")
        return fact

    def validate_outbound_lesson(self, lesson: InferredLesson) -> InferredLesson:
        _assert_no_forbidden_metadata(lesson, f"InferredLesson({lesson.lesson_id})")
        return lesson

    def validate_inbound_context(self, context: AdvisoryContext) -> AdvisoryContext:
        if context.advisory_only is not True:
            raise MemoryAuthorityViolationError(
                "AdvisoryContext must have advisory_only=True"
            )

        for fact in context.facts:
            _assert_no_forbidden_metadata(fact, f"ObservedFact({fact.fact_id})")

        for lesson in context.approved_lessons:
            if not lesson.is_policy_eligible:
                raise MemoryAuthorityViolationError(
                    f"Unapproved or non-policy-eligible lesson {lesson.lesson_id!r} "
                    f"(status={lesson.review_status.value!r}) cannot appear in approved_lessons"
                )
            _assert_no_forbidden_metadata(lesson, f"InferredLesson({lesson.lesson_id})")

        for lesson in context.advisory_unapproved_lessons:
            _assert_no_forbidden_metadata(lesson, f"InferredLesson({lesson.lesson_id})")

        return context

    def assert_read_only_for_authoritative_state(self, operation: str) -> None:
        normalized = re.sub(r"[\s\-]+", "_", operation.strip().lower())
        for target in AUTHORITATIVE_STATE_TARGETS:
            if target in normalized:
                if normalized.startswith(_READ_ONLY_PREFIXES) and not any(
                    kw in normalized for kw in _MUTATION_KEYWORDS
                ):
                    return
                raise MemoryAuthorityViolationError(
                    f"Memory operation {operation!r} is forbidden from mutating "
                    f"authoritative state ({target})"
                )
