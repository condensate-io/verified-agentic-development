from typing import Any

from vad.contracts.models import EIP


def _sorted_scalars(values: list[str]) -> list[str]:
    return sorted(values)


def normalize_eip(eip: EIP) -> dict[str, Any]:
    data = eip.model_dump(mode="json")

    data["non_goals"] = _sorted_scalars(data["non_goals"])
    data["scope_boundaries"] = _sorted_scalars(data["scope_boundaries"])

    for section in ("invariants", "constraints"):
        for key, values in data[section].items():
            data[section][key] = _sorted_scalars(values)

    data["proof_obligations"] = sorted(
        data["proof_obligations"],
        key=lambda item: (item["id"], item["kind"], item["description"]),
    )
    data["tool_permissions"]["allowed"] = _sorted_scalars(data["tool_permissions"]["allowed"])
    data["tool_permissions"]["denied"] = _sorted_scalars(data["tool_permissions"]["denied"])
    data["memory_requirements"] = sorted(
        data["memory_requirements"],
        key=lambda item: (item["scope"], item["purpose"], item["max_payload_size"]),
    )
    data["release_requirements"]["gates"] = _sorted_scalars(data["release_requirements"]["gates"])
    data["telemetry_requirements"]["signals"] = _sorted_scalars(data["telemetry_requirements"]["signals"])

    return data
