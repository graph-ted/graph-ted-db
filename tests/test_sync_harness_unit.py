"""Harness backstop, log names, and secret scrubbing. No rclone and no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "sync_harness_unit", ROOT / "scripts" / "sync_test" / "sync_harness.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sync_harness_unit"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _writer(mod, tmp_path: Path):
    cfg = mod.Config(
        remote="remote:",
        folder="graph-ted-sync-test",
        work=tmp_path / "work",
        results=tmp_path / "results",
        rclone="rclone",
        extra_flags=[],
        run_id="run-test",
    )
    writer = mod.Writer(cfg, "s2", "A", "remote:graph-ted-sync-test/run-test/s2/A")
    writer.synced_once = True
    return writer


def test_scrub_secrets_drops_upload_query_and_keeps_the_error() -> None:
    mod = _harness()
    raw = (
        "ERROR : nodes/0f.w0123456789abcdef.000001.jsonl: "
        "can't copy - source file is being updated (size changed from 637 to 956): "
        "https://microsoftpersonalcontent.com/personal/x/_api/upload"
        "?access_token=SECRETVALUE&tempauth=abc\n"
    )
    clean = mod.scrub_secrets(raw)
    assert "SECRETVALUE" not in clean
    assert "tempauth=abc" not in clean
    assert "size changed from 637 to 956" in clean
    assert "microsoftpersonalcontent.com" in clean
    assert "?REDACTED" in clean
    also = mod.scrub_secrets("see https://example.test/file?sig=abc123&n=1")
    assert "sig=REDACTED" in also
    assert "abc123" not in also


def test_log_excerpt_keeps_a_long_error_line() -> None:
    mod = _harness()
    error = "ERROR can't copy - source file is being updated (size changed from 637 to 956) " + (
        "x" * 4000
    )
    text = ("info\n" * 50) + error + "\n" + ("tail\n" * 20)
    excerpt = mod.log_excerpt(text, tail=200)
    assert "size changed from 637 to 956" in excerpt
    assert "x" * 4000 in excerpt
    nested = mod.scrub_obj(
        {
            "logs": [
                {
                    "out": "size changed from 637 to 956",
                    "url": "https://example.test/q?token=SECRETVALUE",
                }
            ]
        }
    )
    assert nested["logs"][0]["url"].endswith("token=REDACTED")
    assert "SECRETVALUE" not in str(nested)


def test_stream_scrubbed_redacts_each_line_before_it_reaches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill between rclone lines must not leave an access token in the log."""
    mod = _harness()
    log_path = tmp_path / "rclone.log"

    class Drip:
        def __init__(self, cmd, **_kwargs):
            del cmd
            self.returncode = None
            self.stdout = self._lines()

        def _lines(self):
            yield (
                "GET https://microsoftpersonalcontent.com/personal/x/_api/upload"
                "?access_token=SECRETVALUE&tempauth=abc\n"
            )
            # The previous line has already been flushed. A kill here is the
            # case a post-exit scrub would miss.
            on_disk = log_path.read_text(encoding="utf-8")
            assert "SECRETVALUE" not in on_disk
            assert "tempauth=abc" not in on_disk
            assert "size changed from 1 to 2" not in on_disk
            yield "ERROR : size changed from 1 to 2\n"
            self.returncode = 0

        def wait(self, timeout=None):
            del timeout
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mod.subprocess, "Popen", Drip)
    rc, text = mod._stream_scrubbed(["rclone", "bisync"], log_path, timeout=5)
    assert rc == 0
    assert "SECRETVALUE" not in text
    assert "SECRETVALUE" not in log_path.read_text(encoding="utf-8")
    assert "size changed from 1 to 2" in text


def test_rc7_lockout_runs_one_resync_and_stays_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _harness()
    writer = _writer(mod, tmp_path)
    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))
            if len(calls) == 1:
                (writer.workdir / "listing.lst-err").write_text("critical\n", encoding="utf-8")
                text = (
                    "ERROR : can't copy - source file is being updated "
                    "(size changed from 637 to 956) "
                    "https://microsoftpersonalcontent.com/x?access_token=SECRETVALUE\n"
                )
                self.returncode = 7
            else:
                text = "Bisync successful\n"
                self.returncode = 0
            self.stdout = iter([text])

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mod.subprocess, "Popen", FakeProc)
    rc = writer.sync()
    assert rc == 7
    assert len(calls) == 2
    assert "--resync" not in calls[0]
    assert calls[1].count("--resync") == 1
    assert writer.sync_log[0]["failure"] is True
    assert writer.sync_log[0]["critical_lockout"] is True
    assert writer.sync_log[1]["auto_recovery"] is True
    assert writer.sync_log[1]["failure"] is True
    assert writer.sync_log[1]["counts_as_success"] is False
    assert writer.synced_once is True
    names = sorted(path.name for path in writer.logdir.glob("*.log"))
    assert names == [
        "run-test-0001-sync.log",
        "run-test-0002-auto-resync.log",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in writer.logdir.glob("*.log"))
    assert "SECRETVALUE" not in combined
    assert "size changed from 637 to 956" in writer.sync_log[0]["out"]
    # A later phase gets its own file. It does not reopen the lockout log.
    class OkProc:
        def __init__(self, cmd, **_kwargs):
            calls.append(list(cmd))
            self.stdout = iter(["ok\n"])
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(mod.subprocess, "Popen", OkProc)
    assert writer.sync(extra=["--force"]) == 0
    force_logs = list(writer.logdir.glob("*-force.log"))
    assert len(force_logs) == 1
    assert force_logs[0].name == "run-test-0003-force.log"
    assert "size changed from 637 to 956" in (writer.logdir / names[0]).read_text(encoding="utf-8")
