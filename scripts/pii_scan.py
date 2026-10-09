"""Scan the repository for personal data that must not ship.

Committed rules are generic. Real names, account emails, and sync-remote
names are never committed; they come from the environment or an untracked
file so the scan can look for them locally without publishing them:

    GTDB_PII_TERMS      comma-separated words or emails to flag (case-insensitive)
    GTDB_PII_REMOTES    comma-separated rclone remote names; flags "<name>:" usage
    .pii-terms.local    optional, gitignored; one entry per line:
                            term:<word or email>
                            remote:<rclone remote name>

Findings print as ``path:line: rule``. A local term is reported by its index,
never by its value, so CI logs and pasted output stay clean.

    python scripts/pii_scan.py            # tracked files
    python scripts/pii_scan.py --history  # also every commit message, author, and patch
    python scripts/pii_scan.py FILE ...   # just these files
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TERMS_FILE = ".pii-terms.local"

# Generic patterns. Keep them specific enough that docs which merely name a
# sync product (for example "OneDrive conflict copies") do not trip them.
GENERIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("onedrive-share-link", re.compile(r"(?i)\b(?:onedrive\.live\.com|1drv\.ms)/")),
    ("sharepoint-personal-path", re.compile(r"(?i)[a-z0-9-]+-my\.sharepoint\.com/personal/")),
    (
        "onedrive-local-path",
        re.compile(r"(?i)(?:[~/\\]|[a-z]:\\)(?:[^\s/\\]+[/\\])*OneDrive(?: - [^/\\\n]+)?[/\\]"),
    ),
    ("onedrive-mount-path", re.compile(r"(?i)/(?:mnt|media|run/user/\d+)/[^\s/]*onedrive[^\s/]*/")),
    (
        "home-path-unix",
        re.compile(r"(?<![\w.])/home/(?!runner/|user/|<|\$|\{|you/|me/)[a-z_][\w.-]*/"),
    ),
    (
        "home-path-mac",
        re.compile(r"(?<![\w.])/Users/(?!Shared/|<|\$|\{|you/|me/|user/|runner/)[A-Za-z_][\w.-]*/"),
    ),
    (
        "home-path-windows",
        re.compile(
            r"(?i)\b[a-z]:\\{1,2}Users\\{1,2}(?!<|%|\{|\$|you\\|user\\|public\\|default\\|runneradmin\\)[\w.-]+"
        ),
    ),
    (
        "personal-email",
        re.compile(
            r"(?i)\b[\w.+-]+@(?:gmail|googlemail|outlook|hotmail|live|msn|icloud|me|yahoo|proton|protonmail|aol)\.[a-z.]{2,6}\b"
        ),
    ),
    ("rclone-config-token", re.compile(r'(?i)"(?:access_token|refresh_token)"\s*:\s*"[^"]{8,}')),
    ("rclone-drive-id", re.compile(r"(?im)^\s*drive_id\s*=\s*\S+")),
]

SKIP_SUFFIXES = {".png", ".ico", ".woff2", ".woff", ".ttf", ".jpg", ".jpeg", ".gif", ".pdf", ".zip"}
# This file and its test describe the patterns; they contain no real data.
SELF_PATHS = {"scripts/pii_scan.py", "tests/test_pii_scan.py"}
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def load_local_rules(
    root: Path = ROOT, env: dict[str, str] | None = None
) -> list[tuple[str, re.Pattern[str]]]:
    env = dict(os.environ if env is None else env)
    terms: list[str] = [t for t in env.get("GTDB_PII_TERMS", "").split(",") if t.strip()]
    remotes: list[str] = [r for r in env.get("GTDB_PII_REMOTES", "").split(",") if r.strip()]
    local = root / LOCAL_TERMS_FILE
    if local.is_file():
        for raw in local.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            kind, sep, value = line.partition(":")
            if sep and kind == "remote":
                remotes.append(value)
            elif sep and kind == "term":
                terms.append(value)
            else:
                terms.append(line)
    rules: list[tuple[str, re.Pattern[str]]] = []
    for i, term in enumerate(t.strip() for t in terms):
        if term:
            rules.append((f"local-term#{i + 1}", re.compile(re.escape(term), re.IGNORECASE)))
    for i, name in enumerate(r.strip().rstrip(":") for r in remotes):
        if name:
            rules.append(
                (f"local-remote#{i + 1}", re.compile(rf"(?i)(?<![\w-]){re.escape(name)}:"))
            )
    return rules


def scan_text(path: str, text: str, rules: list[tuple[str, re.Pattern[str]]]) -> list[Finding]:
    out: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in rules:
            if pattern.search(line):
                out.append(Finding(path, lineno, rule))
    return out


def tracked_files(root: Path = ROOT) -> list[str]:
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [p for p in proc.stdout.decode().split("\0") if p]


def scan_files(paths: list[str], rules, root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for rel in paths:
        norm = rel.replace(os.sep, "/")
        if norm in SELF_PATHS and not any(r[0].startswith("local-") for r in rules):
            continue
        p = root / rel
        if p.suffix.lower() in SKIP_SUFFIXES or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        active = (
            rules if norm not in SELF_PATHS else [r for r in rules if r[0].startswith("local-")]
        )
        findings.extend(scan_text(norm, text, active))
    return findings


def scan_history(rules, root: Path = ROOT) -> list[Finding]:
    fmt = "--format=commit %H%nauthor %an <%ae>%ncommitter %cn <%ce>%n%B"
    proc = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color", fmt], cwd=root, capture_output=True, check=True
    )
    text = proc.stdout.decode("utf-8", errors="replace")
    local_only = [r for r in rules if r[0].startswith("local-")]
    findings: list[Finding] = []
    commit = "?"
    current: str | None = None  # file whose patch we are in; None = commit header
    for line in text.splitlines():
        if line.startswith("commit ") and len(line) == 47:
            commit = line[7:19]
            current = None
            continue
        if line.startswith("diff --git "):
            m = _DIFF_HEADER.match(line)
            current = m.group(2) if m else None
        # Like the tracked-file scan: this script and its test describe the
        # patterns with synthetic examples, so only local terms apply there.
        active = local_only if current in SELF_PATHS else rules
        for rule, pattern in active:
            if pattern.search(line):
                findings.append(Finding(f"history:{commit}", 0, rule))
    # one finding per commit and rule is enough
    return sorted(set(findings), key=lambda f: (f.path, f.rule))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Flag personal data in the repository.")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--history", action="store_true", help="also scan every commit")
    args = parser.parse_args(argv)
    rules = GENERIC_RULES + load_local_rules()
    files = args.files or tracked_files()
    findings = scan_files(files, rules)
    if args.history:
        findings.extend(scan_history(rules))
    for f in findings:
        print(f)
    local_count = sum(1 for r in rules if r[0].startswith("local-"))
    print(
        f"pii_scan: {len(findings)} finding(s); {len(GENERIC_RULES)} generic rules, "
        f"{local_count} local rules",
        file=sys.stderr,
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
