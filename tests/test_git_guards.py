"""Tests for the revamp git workflow guards (scripts/git/guards.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "git" / "guards.py"
_spec = importlib.util.spec_from_file_location("guards", _PATH)
assert _spec and _spec.loader
guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guards)


@pytest.mark.parametrize(
    "subject",
    [
        "feat(thesis): add append-only thesis revisions",
        "fix: root-cause starttls failure on port 587",
        "refactor(db)!: split storage module",
        "chore(git): merge revamp-main into feat/s2-founder-card",
        "ci(security): refuse pushes to main",
        "revert: undo graph projection change",
    ],
)
def test_valid_subjects(subject: str) -> None:
    assert guards.check_subject(subject) == []


@pytest.mark.parametrize(
    "subject",
    [
        "Initial public release",
        "Merge pull request #3 from org/branch",
        "gnhf 4: tidy things",
        "feature: add thing",
        "feat: Add thing",
        "feat: add thing.",
        "feat:add thing",
        "feat(Thesis): add thing",
        "feat: " + "x" * 80,
    ],
)
def test_invalid_subjects(subject: str) -> None:
    assert guards.check_subject(subject)


def test_message_requires_blank_line_and_ignores_comments() -> None:
    assert guards.check_message("# comment\nfeat: add x\n\nbody\n\nRefs: #1\n") == []
    assert guards.check_message("feat: add x\nbody right away") != []
    assert guards.check_message("# only comments\n") == ["empty commit message"]


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("feat/s1-thesis-engine", True),
        ("refactor/s0-split-db-module", True),
        ("ci/s0-git-guards", True),
        ("gnhf/overnight", False),
        ("feat/thesis-engine", False),
        ("Feat/s1-thesis", False),
        ("feat/s1-Thesis_engine", False),
    ],
)
def test_branch_names(name: str, ok: bool) -> None:
    assert (guards.check_branch(name) == []) is ok


def test_pr_base() -> None:
    assert guards.check_pr_base("main")
    assert guards.check_pr_base("refs/heads/main")
    assert guards.check_pr_base("revamp-main") == []
    assert guards.check_pr_base("feat/s1-thesis-engine") == []


def test_push_refuses_protected_branches_on_any_remote() -> None:
    for remote in ("origin", "no-mistakes", "fork"):
        assert any("main" in p for p in guards.check_push(remote, ["refs/heads/main"]))
        problems = guards.check_push(remote, ["refs/heads/revamp-main"])
        assert any("revamp-main" in p for p in problems)


def test_push_requires_gate_remote_for_feature_branches() -> None:
    assert guards.check_push("no-mistakes", ["refs/heads/feat/s1-thesis-engine"]) == []
    problems = guards.check_push("origin", ["refs/heads/feat/s1-thesis-engine"])
    assert problems and "no-mistakes" in problems[0]


def test_push_rejects_bad_branch_name_even_through_gate() -> None:
    assert guards.check_push("no-mistakes", ["refs/heads/gnhf/run-1"])


def test_cli_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good"
    good.write_text("docs(revamp): add findings\n", encoding="utf-8")
    bad = tmp_path / "bad"
    bad.write_text("Update stuff\n", encoding="utf-8")
    assert guards.main(["commit-msg", str(good)]) == 0
    assert guards.main(["commit-msg", str(bad)]) == 1
    assert guards.main(["--unknown"]) == 2
