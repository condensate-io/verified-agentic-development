import pytest
from pydantic import ValidationError

from vad.control_plane.events import ControlPlaneEvent, ControlPlaneEventKind, ControlPlaneEventStatus


def test_control_plane_event_envelope_validates_required_fields():
    event = ControlPlaneEvent(
        sequence=1,
        client_id="codex-local",
        run_id="run-1",
        task_id="task-1",
        kind=ControlPlaneEventKind.TOOL_CALL_STARTED,
        status=ControlPlaneEventStatus.ACTIVE,
        actor="builder",
        role="builder",
        evidence_digest="a" * 64,
        summary="Codex started repo assessment.",
    )

    assert event.event_id.startswith("control-plane-event-")
    assert event.sequence == 1
    assert event.kind == ControlPlaneEventKind.TOOL_CALL_STARTED
    assert event.status == ControlPlaneEventStatus.ACTIVE


def test_control_plane_event_rejects_unknown_kind_and_status():
    with pytest.raises(ValidationError):
        ControlPlaneEvent(
            sequence=1,
            client_id="codex-local",
            kind="unknown_kind",
            status="active",
            actor="builder",
            role="builder",
            summary="bad kind",
        )

    with pytest.raises(ValidationError):
        ControlPlaneEvent(
            sequence=1,
            client_id="codex-local",
            kind="heartbeat",
            status="unknown_status",
            actor="builder",
            role="builder",
            summary="bad status",
        )


def test_control_plane_event_rejects_unsafe_identifiers_and_bad_digest():
    with pytest.raises(ValidationError, match="identifier must not contain"):
        ControlPlaneEvent(
            sequence=1,
            client_id="../escape",
            kind="heartbeat",
            status="active",
            actor="builder",
            role="builder",
            summary="unsafe id",
        )

    with pytest.raises(ValidationError):
        ControlPlaneEvent(
            sequence=1,
            client_id="codex-local",
            kind="heartbeat",
            status="active",
            actor="builder",
            role="builder",
            evidence_digest="not-a-digest",
            summary="bad digest",
        )


def test_control_plane_event_schema_contains_core_fields():
    schema = ControlPlaneEvent.json_schema()
    properties = schema["properties"]

    for field in [
        "event_id",
        "sequence",
        "created_at",
        "client_id",
        "run_id",
        "task_id",
        "kind",
        "status",
        "actor",
        "role",
        "evidence_digest",
        "summary",
    ]:
        assert field in properties
    assert "sequence" in schema["required"]
    assert "client_id" in schema["required"]
    assert "kind" in schema["required"]
