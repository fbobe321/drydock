"""Skills: user-defined reusable /<name> slash commands loaded from markdown."""
from __future__ import annotations

from drydock import skills


def _write(d, name, content):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(content)


def test_load_parses_frontmatter_and_body(tmp_path):
    _write(tmp_path / ".drydock" / "skills", "review.md",
           "---\nname: review\ndescription: Review the diff\n---\nRun GitDiff and review it.")
    sk = skills.load_skills(str(tmp_path))
    assert "review" in sk
    assert sk["review"].description == "Review the diff"
    assert sk["review"].body == "Run GitDiff and review it."


def test_name_defaults_to_filename(tmp_path):
    _write(tmp_path / ".drydock" / "skills", "explain.md", "Explain the selected code.")
    sk = skills.load_skills(str(tmp_path))
    assert "explain" in sk and sk["explain"].body == "Explain the selected code."


def test_render_appends_args_without_placeholder(tmp_path):
    _write(tmp_path / ".drydock" / "skills", "t.md", "Do the thing.")
    sk = skills.load_skills(str(tmp_path))["t"]
    assert sk.render("on auth.py") == "Do the thing.\n\non auth.py"
    assert sk.render("") == "Do the thing."


def test_render_substitutes_args_placeholder(tmp_path):
    _write(tmp_path / ".drydock" / "skills", "t.md", "Focus on $ARGS and nothing else.")
    sk = skills.load_skills(str(tmp_path))["t"]
    assert sk.render("performance") == "Focus on performance and nothing else."


def test_project_overrides_user(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    monkeypatch.setattr(skills.Path, "home", lambda: home)
    _write(home / ".drydock" / "skills", "x.md", "USER version")
    _write(proj / ".drydock" / "skills", "x.md", "PROJECT version")
    sk = skills.load_skills(str(proj))
    assert sk["x"].body == "PROJECT version"


def test_empty_body_skipped(tmp_path):
    _write(tmp_path / ".drydock" / "skills", "empty.md", "---\nname: empty\n---\n")
    assert "empty" not in skills.load_skills(str(tmp_path))


def test_no_user_or_project_skills_yields_only_builtins(tmp_path):
    # With no user/project skills, only the bundled built-ins (RMF) are present.
    loaded = skills.load_skills(str(tmp_path))
    assert set(loaded) == {"rmf-control", "rmf-categorize", "rmf-review", "rmf-poam", "stig-assess", "stig-remediate"}


def test_create_skill_user_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    path = skills.create_skill("review", "Run GitDiff then review for bugs.",
                               description="Review the diff")
    assert path.exists()
    loaded = skills.load_skills(str(tmp_path / "proj"))  # user-scope visible anywhere
    assert "review" in loaded and "review for bugs" in loaded["review"].body
    assert loaded["review"].description == "Review the diff"


def test_create_skill_rejects_bad_name_and_empty_body(tmp_path, monkeypatch):
    monkeypatch.setattr(skills.Path, "home", lambda: tmp_path)
    import pytest
    with pytest.raises(ValueError):
        skills.create_skill("bad name!", "body")
    with pytest.raises(ValueError):
        skills.create_skill("ok", "   ")


def test_create_skill_project_scope(tmp_path):
    path = skills.create_skill("explain", "Explain $ARGS.", scope="project", cwd=str(tmp_path))
    assert str(tmp_path) in str(path)
    assert skills.load_skills(str(tmp_path))["explain"].render("auth.py") == "Explain auth.py."
