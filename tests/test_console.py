"""Operator-surface receipt: `deck console` must actually serve a human face.

STUDENT nudge 2026-09-03: the quickstart's one command with no test coverage.
This boots the real CLI front door as a subprocess (the way an operator runs
it), fetches /index.html over HTTP, and asserts the console shell arrives.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_console_serves_index():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "deck", "console", "--port", str(port)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # poll until the socket answers (or the child dies -> fail loud)
        deadline = time.time() + 10
        last_err = None
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"console exited rc={proc.returncode}: {out}")
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/index.html", timeout=2) as r:
                    body = r.read().decode("utf-8", "replace")
                    assert r.status == 200, f"HTTP {r.status}"
                    break
            except OSError as e:
                last_err = e
                time.sleep(0.2)
        else:
            raise AssertionError(f"console never answered :{port}: {last_err}")
        assert "deck" in body.lower(), "index.html does not look like the console"
        # and the shell references its assets (a face, not a blank page)
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/app.js", timeout=2) as r:
            assert r.status == 200
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
