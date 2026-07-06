import json
import sys
from unittest.mock import patch

import pytest

from vad.cli import main


def test_cli_sign_evidence_writes_signed_envelope(tmp_path):
    evidence_file = tmp_path / "evidence.json"
    secret_file = tmp_path / "secret.key"
    signed_file = tmp_path / "signed.json"
    evidence_file.write_text(json.dumps({"run_id": "run-1", "final_decision": "passed"}), encoding="utf-8")
    secret_file.write_bytes(b"secret")

    with patch.object(sys, "argv", [
        "vad", "sign", "evidence", str(evidence_file), "--key-id", "local-dev", "--secret-file", str(secret_file), "--out", str(signed_file),
    ]):
        main()

    payload = json.loads(signed_file.read_text(encoding="utf-8"))
    assert payload["payload"]["run_id"] == "run-1"
    assert payload["signature"]["key_id"] == "local-dev"
    assert "secret" not in signed_file.read_text(encoding="utf-8")


def test_cli_sign_verify_accepts_valid_envelope(tmp_path, capsys):
    evidence_file = tmp_path / "evidence.json"
    secret_file = tmp_path / "secret.key"
    signed_file = tmp_path / "signed.json"
    evidence_file.write_text(json.dumps({"run_id": "run-1", "final_decision": "passed"}), encoding="utf-8")
    secret_file.write_bytes(b"secret")

    with patch.object(sys, "argv", [
        "vad", "sign", "evidence", str(evidence_file), "--key-id", "local-dev", "--secret-file", str(secret_file), "--out", str(signed_file),
    ]):
        main()
    with patch.object(sys, "argv", ["vad", "sign", "verify", str(signed_file), "--secret-file", str(secret_file)]):
        main()

    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is True
    assert result["key_id"] == "local-dev"


def test_cli_sign_verify_rejects_tampered_envelope(tmp_path, capsys):
    evidence_file = tmp_path / "evidence.json"
    secret_file = tmp_path / "secret.key"
    signed_file = tmp_path / "signed.json"
    evidence_file.write_text(json.dumps({"run_id": "run-1", "final_decision": "passed"}), encoding="utf-8")
    secret_file.write_bytes(b"secret")

    with patch.object(sys, "argv", [
        "vad", "sign", "evidence", str(evidence_file), "--key-id", "local-dev", "--secret-file", str(secret_file), "--out", str(signed_file),
    ]):
        main()
    signed = json.loads(signed_file.read_text(encoding="utf-8"))
    signed["payload"]["final_decision"] = "failed"
    signed_file.write_text(json.dumps(signed), encoding="utf-8")

    with patch.object(sys, "argv", ["vad", "sign", "verify", str(signed_file), "--secret-file", str(secret_file)]):
        with pytest.raises(SystemExit) as e:
            main()

    assert e.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is False
