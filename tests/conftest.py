"""Stop a leaked non-daemon thread from pinning the interpreter after pytest.

Python waits for those threads only once the session summary has been
printed, so CI shows a passing run that never exits. Daemon threads are
not part of that wait.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading


def pytest_sessionfinish(session, exitstatus: int) -> None:
    del session
    leaked = [
        thread
        for thread in threading.enumerate()
        if thread is not threading.main_thread() and not thread.daemon and thread.is_alive()
    ]
    if not leaked:
        return
    print("non-daemon threads still running after tests:", file=sys.stderr)
    for thread in leaked:
        print(f"  {thread.name} ident={thread.ident}", file=sys.stderr)
    sys.stderr.flush()
    faulthandler.dump_traceback(all_threads=True)
    os._exit(exitstatus if exitstatus else 1)
