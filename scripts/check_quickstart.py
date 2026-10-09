"""Run the README quick start verbatim in a scratch folder.

Extracts the first ```python block and the first ```bash block under
"## Quick start" in README.md and runs them as written, using the
interpreter and console script from the current environment. Used by the CI
install job against a built wheel and sdist, so it must only use the
standard library.

    python scripts/check_quickstart.py [README.md]
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def blocks(readme: str, lang: str) -> list[str]:
    section = readme.split("## Quick start", 1)[1].split("\n## ", 1)[0]
    return re.findall(rf"```{lang}\n(.*?)```", section, flags=re.S)


def main() -> int:
    readme = Path(sys.argv[1] if len(sys.argv) > 1 else "README.md").read_text(encoding="utf-8")
    py = blocks(readme, "python")[0]
    sh = blocks(readme, "bash")[0]
    bindir = Path(sys.executable).parent
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run([sys.executable, "-c", py], cwd=tmp, capture_output=True, text=True)
        print(out.stdout, out.stderr, sep="", end="")
        if out.returncode != 0 or out.stdout.strip() != "Alice":
            print("quick start (python) failed", file=sys.stderr)
            return 1
        shutil.rmtree(Path(tmp) / "my-graph")
        for line in sh.strip().splitlines():
            argv = shlex.split(line)
            exe = shutil.which(argv[0], path=str(bindir)) or shutil.which(argv[0])
            if exe is None:
                print(f"console script not found: {argv[0]}", file=sys.stderr)
                return 1
            out = subprocess.run([exe, *argv[1:]], cwd=tmp, capture_output=True, text=True,
                                 env={**os.environ, "PYTHONUTF8": "1"})
            print(f"$ {line}\n{out.stdout}{out.stderr}", end="")
            if out.returncode != 0:
                print(f"quick start (cli) failed: {line}", file=sys.stderr)
                return 1
    print("quick start OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
