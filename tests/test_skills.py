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


def test_no_skills_dir_is_empty(tmp_path):
    assert skills.load_skills(str(tmp_path)) == {}
