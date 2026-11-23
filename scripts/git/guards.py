"""Git workflow guards for the venture-intelligence revamp.

One checker shared by the local hooks (``.githooks/``) and CI so the rules
cannot drift:

* Conventional Commits 1.0.0 for every commit subject and PR title.
* Branch names ``<type>/<slice>-<kebab-description>``.
* No push or PR may target ``main``; no direct push may target ``revamp-main``.
* No plain ``git push origin`` from a working checkout: feature branches go
  through the ``no-mistakes`` gate remote.

Standard library only so the hooks run without the project virtualenv.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence

TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
_TYPES_RE = "|".join(TYPES)

# <type>[optional scope][!]: <description>
SUBJECT_RE = re.compile(
    rf"^(?P<type>{_TYPES_RE})"
    r"(?:\((?P<scope>[a-z0-9][a-z0-9._/-]*)\))?"
    r"(?P<breaking>!)?"
    r": (?P<description>\S.*)$"
)
# <type>/<slice>-<kebab-description>, e.g. feat/s1-thesis-engine
BRANCH_RE = re.compile(rf"^(?:{_TYPES_RE})/s\d+-[a-z0-9]+(?:-[a-z0-9]+)*$")

MAX_SUBJECT = 72
PROTECTED_PUSH = frozenset({"main", "revamp-main"})
PROTECTED_BASE = frozenset({"main"})
GATE_REMOTE = "no-mistakes"
EXIT_OK, EXIT_VIOLATION, EXIT_USAGE = 0, 1, 2


def check_subject(subject: str) -> list[str]:
    """Return problems with a commit subject or PR title (empty when valid)."""
    subject = subject.rstrip("\r\n")
    problems: list[str] = []
    match = SUBJECT_RE.match(subject)
    if not match:
        return [
            f"not a Conventional Commit: {subject!r} "
            f"(expected '<type>[scope][!]: <description>', type in {', '.join(TYPES)})"
        ]
    description = match.group("description")
    if description[0].isupper():
        problems.append(f"description must start lowercase: {subject!r}")
    if description.endswith("."):
        problems.append(f"description must not end with a period: {subject!r}")
    if len(subject) > MAX_SUBJECT:
        problems.append(f"subject is {len(subject)} chars, keep it <= {MAX_SUBJECT}: {subject!r}")
    return problems


def check_message(message: str) -> list[str]:
    """Validate a full commit message (subject, blank line, body, footers)."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ["empty commit message"]
    problems = check_subject(lines[0])
    if len(lines) > 1 and lines[1].strip():
        problems.append("separate the subject from the body with a blank line")
    return problems


def check_branch(name: str) -> list[str]:
    name = name.removeprefix("refs/heads/")
    if BRANCH_RE.match(name):
        return []
    return [
        f"branch {name!r} must look like '<type>/s<N>-<kebab-description>' "
        f"(e.g. feat/s1-thesis-engine), type in {', '.join(TYPES)}"
    ]


def check_pr_base(base: str) -> list[str]:
    base = base.removeprefix("refs/heads/")
    if base in PROTECTED_BASE:
        return [
            f"pull requests must not target {base!r}; "
            "target revamp-main or the previous stack branch"
        ]
    return []


def check_push(remote: str, remote_refs: Iterable[str]) -> list[str]:
    """Refuse pushes to protected branches and plain pushes to origin."""
    problems: list[str] = []
    for ref in remote_refs:
        branch = ref.removeprefix("refs/heads/")
        if branch in PROTECTED_PUSH:
            problems.append(
                f"direct push to {branch!r} is forbidden; open a PR from a feature branch"
            )
            continue
        if ref.startswith("refs/heads/"):
            problems.extend(check_branch(branch))
    pushes_branches = any(r.startswith("refs/heads/") for r in remote_refs)
    if remote != GATE_REMOTE and not problems and pushes_branches:
        problems.append(
            f"push branches through the gate: 'git push {GATE_REMOTE} <branch>' (not '{remote}')"
        )
    return problems


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def check_range(*rev_args: str) -> list[str]:
    """Validate every commit subject selected by ``git log <rev_args>``."""
    problems: list[str] = []
    for line in _git("log", "--format=%H%x00%s", *rev_args).splitlines():
        sha, _, subject = line.partition("\x00")
        problems.extend(f"{sha[:10]}: {p}" for p in check_subject(subject))
    return problems


def _pre_push(remote: str, stdin_lines: Sequence[str]) -> list[str]:
    zero = "0" * 40
    remote_refs: list[str] = []
    problems: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, remote_ref, remote_sha = parts
        remote_refs.append(remote_ref)
        if local_sha == zero or not remote_ref.startswith("refs/heads/"):
            continue  # deletions and tags carry no new commits to lint
        if remote_sha == zero:
            # New branch: lint only commits not already on a remote branch.
            problems.extend(check_range(local_sha, "--not", "--remotes"))
        else:
            problems.extend(check_range(f"{remote_sha}..{local_sha}"))
    return check_push(remote, remote_refs) + problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guards", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("commit-msg").add_argument("file")
    sub.add_parser("subject").add_argument("text")
    sub.add_parser("branch").add_argument("name")
    sub.add_parser("pr-base").add_argument("base")
    sub.add_parser("range").add_argument("rev_range")
    pre_push = sub.add_parser("pre-push")
    pre_push.add_argument("remote")
    pre_push.add_argument("url", nargs="?")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors already
        return int(exc.code or 0)

    if args.cmd == "commit-msg":
        with open(args.file, encoding="utf-8") as handle:
            problems = check_message(handle.read())
    elif args.cmd == "subject":
        problems = check_subject(args.text)
    elif args.cmd == "branch":
        problems = check_branch(args.name)
    elif args.cmd == "pr-base":
        problems = check_pr_base(args.base)
    elif args.cmd == "range":
        problems = check_range(args.rev_range)
    else:
        problems = _pre_push(args.remote, sys.stdin.read().splitlines())

    for problem in problems:
        print(f"guard: {problem}", file=sys.stderr)
    return EXIT_VIOLATION if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
