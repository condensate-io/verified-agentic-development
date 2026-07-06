import pytest

from vad.evidence.bundle import PatchJournalEvidence
from vad.repo.journal import PatchJournal


def test_patch_journal_records_changed_files_and_digest(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")

    journal = PatchJournal.capture(tmp_path, ["app.py"])

    target.write_text("after\n", encoding="utf-8")
    evidence = journal.to_evidence()

    assert evidence.changed_files == ["app.py"]
    assert len(evidence.patch_digest) == 64
    assert PatchJournalEvidence(**evidence.model_dump())


def test_patch_journal_rollback_restores_existing_file(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    journal = PatchJournal.capture(tmp_path, ["app.py"])
    target.write_text("after\n", encoding="utf-8")

    result = journal.rollback()

    assert result.rolled_back is True
    assert target.read_text(encoding="utf-8") == "before\n"
    assert journal.to_evidence(result).rolled_back is True


def test_patch_journal_rollback_removes_created_file(tmp_path):
    journal = PatchJournal.capture(tmp_path, ["created.py"])
    target = tmp_path / "created.py"
    target.write_text("new\n", encoding="utf-8")

    result = journal.rollback()

    assert result.rolled_back is True
    assert not target.exists()


def test_patch_journal_rejects_path_escape(tmp_path):
    with pytest.raises(ValueError, match="path escapes"):
        PatchJournal.capture(tmp_path, ["../outside.py"])
