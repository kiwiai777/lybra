"""AIPOS-211 — v1.0 acceptance: the AUTOMATED regression gate (layer a).

A reproducible, machine-runnable gate over the structural invariants that need NO Owner confirm —
so it is fully automatable WITHOUT holding an owner token or self-confirming (★A1 stays intact;
Supervised confirm remains Owner-out-of-band, covered by the manual runbook, layer b).

This is a pure ACCEPTANCE ARTIFACT: it aggregates the existing tests + structural checks and emits
`ACCEPTANCE: PASS/FAIL`. It does not re-implement gate/copilot logic, never confirms anything, holds
no owner token, makes no external network call, and touches no evidence workspace.

Run:  PYTHONPATH=<repo> python -m tools.acceptance.v1_acceptance
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Acceptance anchors (承 the named slices). Each is an existing test module run with REAL
# serve-rotate credentials (no hand-built registry) — not re-implemented here.
ANCHORS = [
    ("scope reachability (AIPOS-207, real rotate)", "tools.mcp_server.tests.test_scope_reachability"),
    ("card conformance vs real draft_publish_dry_run (AIPOS-208)", "tools.lybra_tui.tests.test_ai_authoring"),
    ("copilot ★A1 + zero-write + RF-5 (AIPOS-206)", "tools.lybra_tui.tests.test_copilot"),
    ("presentation: single token + degradation + isolation (AIPOS-210)", "tools.lybra_tui.tests.test_presentation"),
]

# In a SUBPROCESS with `import textual` blocked, import the gate + start/stop an in-proc gate.
# This proves dependency isolation EVEN in a dev env that has textual installed (RF-5: the PASS must
# not be an artifact of "textual happened to be present"). Exit non-zero ⇒ isolation broken.
_ISOLATION_PROBE = r"""
import sys
class _BlockTextual:
    def find_spec(self, name, path=None, target=None):
        if name == "textual" or name.startswith("textual."):
            raise ImportError("textual blocked for the AIPOS-211 isolation probe")
        return None
sys.meta_path.insert(0, _BlockTextual())
import threading
import tools.mcp_server.tools  # gate tools (must import with no textual)
import tools.aipos_cli.confirm_client  # client (no textual)
import tools.lybra_tui.state, tools.lybra_tui.copilot, tools.lybra_tui.presentation  # tui core (no textual)
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server
cfg = HttpSseConfig(host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01, max_keepalive_events=1)
httpd = build_http_server(cfg)
t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
httpd.shutdown(); t.join(timeout=2); httpd.server_close()
print("GATE_OK_NO_TEXTUAL")
"""


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=str(REPO_ROOT), env={"PYTHONPATH": str(REPO_ROOT), "PATH": _path()},
        capture_output=True, text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def _path() -> str:
    import os
    return os.environ.get("PATH", "")


def _unittest(target: str) -> tuple[bool, str]:
    code, out = _run([sys.executable, "-m", "unittest", target])
    return code == 0, out.strip().splitlines()[-1] if out.strip() else ""


def check_full_suite() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-m", "unittest", "discover", "-s", "tools", "-p", "test_*.py"])
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return code == 0, tail


def check_isolation_grep() -> tuple[bool, str]:
    # Only tools/lybra_tui/app.py may import textual (excluding test files).
    offenders = []
    for path in (REPO_ROOT / "tools").rglob("*.py"):
        # app.py legitimately imports textual; tests + this acceptance probe only mention it as a
        # string (the real no-textual proof is the subprocess probe below).
        if "tests" in path.parts or "acceptance" in path.parts or path.name == "app.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "import textual" in text or "from textual" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    return not offenders, ("only app.py imports textual" if not offenders else f"offenders: {offenders}")


def check_isolation_textual_absent() -> tuple[bool, str]:
    code, out = _run([sys.executable, "-c", _ISOLATION_PROBE])
    ok = code == 0 and "GATE_OK_NO_TEXTUAL" in out
    return ok, ("gate imports + runs with textual blocked" if ok else f"FAILED: {out.strip()[-300:]}")


def run() -> int:
    results: list[tuple[str, bool, str]] = []
    ok, detail = check_full_suite()
    results.append(("full tools/ suite green (core lane)", ok, detail))
    ok, detail = check_isolation_grep()
    results.append(("dependency isolation — only app.py imports textual", ok, detail))
    ok, detail = check_isolation_textual_absent()
    results.append(("dependency isolation — gate runs with textual ABSENT (subprocess probe)", ok, detail))
    for label, module in ANCHORS:
        ok, detail = _unittest(module)
        results.append((label, ok, detail))

    print("\nLybra v1.0 acceptance — automated regression gate (no confirm, no owner token)\n")
    for label, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    overall = all(ok for _, ok, _ in results)
    print(f"\nACCEPTANCE: {'PASS' if overall else 'FAIL'}\n")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(run())
