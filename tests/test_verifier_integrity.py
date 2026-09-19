"""Verifier integrity (drydock/verifier_integrity.py) — mitigation 2 for MCR PRD
Appendix A.4: detect that the files defining the score were edited during the run."""
import subprocess

from drydock.verifier_integrity import (
    changed_files,
    edited_verifier_files,
    integrity_note,
    looks_like_verifier_file,
)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _repo(tmp_path):
    _git(["init", "-q"], tmp_path)
    _git(["config", "user.name", "t"], tmp_path)
    _git(["config", "user.email", "t@t"], tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_a():\n    assert True\n")
    (tmp_path / "src.py").write_text("x = 1\n")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-qm", "init"], tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
                          capture_output=True, text=True).stdout.strip()
    return head


def test_recognises_verifier_paths():
    for p in ("tests/test_x.py", "test/foo.py", "conftest.py", "src/test_thing.py",
              "a/b_test.py", "pyproject.toml", "Makefile", "spec/x.py"):
        assert looks_like_verifier_file(p), p


def test_ignores_ordinary_source():
    for p in ("src/calc.py", "drydock/agent.py", "README.md", "lib/util.py"):
        assert not looks_like_verifier_file(p), p


def test_clean_repo_has_no_edited_verifier_files(tmp_path):
    head = _repo(tmp_path)
    assert edited_verifier_files(str(tmp_path), head) == []
    assert integrity_note([]) == ""


def test_editing_source_only_is_not_flagged(tmp_path):
    head = _repo(tmp_path)
    (tmp_path / "src.py").write_text("x = 2\n")
    assert changed_files(str(tmp_path), head) == ["src.py"]
    assert edited_verifier_files(str(tmp_path), head) == []


def test_deleting_a_test_is_flagged(tmp_path):
    """The observed attack: remove the requirement that cannot be satisfied."""
    head = _repo(tmp_path)
    (tmp_path / "tests" / "test_calc.py").unlink()
    edited = edited_verifier_files(str(tmp_path), head)
    assert edited == ["tests/test_calc.py"]
    note = integrity_note(edited)
    assert "VERIFIER INTEGRITY" in note and "tests/test_calc.py" in note


def test_modifying_a_test_is_flagged(tmp_path):
    head = _repo(tmp_path)
    (tmp_path / "tests" / "test_calc.py").write_text("def test_a():\n    pass\n")
    assert edited_verifier_files(str(tmp_path), head) == ["tests/test_calc.py"]


def test_missing_base_ref_or_non_repo_is_not_evidence(tmp_path):
    """An unavailable check must not masquerade as tampering."""
    assert changed_files(str(tmp_path), "") == []
    assert edited_verifier_files(str(tmp_path), "deadbeef") == []


def test_note_truncates_long_lists():
    note = integrity_note([f"tests/test_{i}.py" for i in range(10)])
    assert "+4 more" in note
