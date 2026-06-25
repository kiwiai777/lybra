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

# AIPOS-218 WS6: Block ALL third-party imports (incl. yaml, textual) — allow only stdlib +
# builtins + the repo's own tools package.  This proves the gate core (init, task/record I/O,
# claim/return/audit with canonical inputs) is correct on bare python with NO third-party deps.
# Combined with correctness assertions below, "R0 zero-dep" means *correct*, not just importable.
_BROAD_BLOCK_PROBE = r"""
import sys, builtins

_STDLIB = getattr(sys, "stdlib_module_names", set())
_BUILTIN_NAMES = set(dir(builtins))

class _BlockThirdParty:
    # Block every import that is not stdlib, a builtin, or the repo tools package.
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if (
            top == "tools"
            or top.startswith("_")
            or top in _STDLIB
            or top in _BUILTIN_NAMES
            or top in {"encodings", "codecs", "abc", "typing_extensions"}
        ):
            return None  # let normal import machinery handle it
        raise ImportError(f"third-party module blocked for AIPOS-218 WS6 zero-dep probe: {name!r}")

sys.meta_path.insert(0, _BlockThirdParty())

# ----- Gate boot + stop (original AIPOS-211 check, now under the broad block) -----
import threading
import tools.mcp_server.tools  # gate tools (must import with no third-party)
import tools.aipos_cli.confirm_client  # client
import tools.lybra_tui.state, tools.lybra_tui.copilot, tools.lybra_tui.presentation  # tui core
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server
cfg = HttpSseConfig(host=DEFAULT_HTTP_HOST, port=0, token="", keepalive_seconds=0.01, max_keepalive_events=1)
httpd = build_http_server(cfg)
t = threading.Thread(target=httpd.serve_forever, daemon=True); t.start()
httpd.shutdown(); t.join(timeout=2); httpd.server_close()
print("GATE_OK_NO_TEXTUAL")  # keep tag for existing check

# ----- AIPOS-218 WS6 correctness assertions -----
import tempfile, json
from pathlib import Path

REPO_ROOT = Path(".").resolve()  # probe is run with cwd=REPO_ROOT via subprocess
TEMPLATES_DIR = REPO_ROOT / "templates"

# (a) A rendered return record round-trips losslessly (no PyYAML, no textual).
from tools.aipos_cli.record_writer import build_mcp_return_record_markdown
from tools.aipos_cli.frontmatter import parse_markdown_frontmatter

md = build_mcp_return_record_markdown(
    task_id="AIPOS-WS6-ACC",
    task_path="5_tasks/queue/claimed/aipos-ws6-acc.md",
    actor="agent-01",
    canonical_agent_instance="agent-01",
    owner_policy_ref="DL-20260625-01",
    return_id="return-ws6-acc",
    claim_id="claim-ws6-acc",
    session_id="session-ws6-acc",
    returned_at="2026-06-25T00:01:00Z",
    result_summary="Fix: colons and all",
    artifact_refs=["docs/out #1.md", "5_tasks/records/r.md"],
    completion_report_ref=None,
)
data, body, warnings = parse_markdown_frontmatter(md)
assert data.get("record_type") == "return_record", f"round-trip (a) failed: record_type={data.get('record_type')!r}"
assert data.get("artifact_refs") == ["docs/out #1.md", "5_tasks/records/r.md"], f"round-trip (a) artifact_refs wrong: {data.get('artifact_refs')!r}"
assert warnings == [], f"round-trip (a) unexpected warnings: {warnings}"
print("CORRECTNESS_A_PASS")

# (b) Every real bundled manifest parses equal to expected nested-map shapes (no PyYAML baseline,
#     but we verify key nested fields exist and have correct Python types).
from tools.aipos_cli.frontmatter import _fallback_parse
manifests = list(TEMPLATES_DIR.rglob("manifest.md"))
assert manifests, "No manifests found under templates/"
for mpath in sorted(manifests):
    mtext = mpath.read_text(encoding="utf-8")
    lines = mtext.splitlines()
    if not lines or lines[0].strip() != "---":
        continue
    fm_text = ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_text = "\n".join(lines[1:i]); break
    mdata, mwarns = _fallback_parse(fm_text)
    assert mwarns == [], f"manifest {mpath.name}: fallback warnings: {mwarns}"
    assert "template_id" in mdata, f"manifest {mpath.name}: missing template_id"
    assert "output_policy" in mdata, f"manifest {mpath.name}: missing output_policy"
    assert isinstance(mdata["output_policy"], dict), f"manifest {mpath.name}: output_policy not a dict"
print("CORRECTNESS_B_PASS")

# (c) lybra init succeeds end-to-end with all third-party blocked.
from tools.aipos_cli.workspace_templates import execute_workspace_init
with tempfile.TemporaryDirectory() as tmp:
    result = execute_workspace_init(
        template="software-development",
        output=Path(tmp) / "ws",
        variables={"project_id": "acc-ws6-test", "source_tag": "acceptance", "external_ref": "none"},
        actor="acceptance-probe",
    )
    assert result.get("ok"), f"lybra init failed: {result.get('blocking_reasons', result)}"
    assert (Path(tmp) / "ws" / "5_tasks" / "queue" / "pending").exists(), "init: task queue not created"
print("CORRECTNESS_C_PASS")
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
    """AIPOS-218 WS6: block ALL third-party (incl. yaml, textual) + assert correctness."""
    code, out = _run([sys.executable, "-c", _BROAD_BLOCK_PROBE])
    # Must print all three CORRECTNESS_x_PASS tokens + GATE_OK_NO_TEXTUAL.
    all_tags = ["GATE_OK_NO_TEXTUAL", "CORRECTNESS_A_PASS", "CORRECTNESS_B_PASS", "CORRECTNESS_C_PASS"]
    missing = [t for t in all_tags if t not in out]
    ok = code == 0 and not missing
    if ok:
        return True, "gate imports + runs with ALL third-party blocked; correctness A/B/C pass"
    return False, f"FAILED (missing={missing}): {out.strip()[-400:]}"


def run() -> int:
    results: list[tuple[str, bool, str]] = []
    ok, detail = check_full_suite()
    results.append(("full tools/ suite green (core lane)", ok, detail))
    ok, detail = check_isolation_grep()
    results.append(("dependency isolation — only app.py imports textual", ok, detail))
    ok, detail = check_isolation_textual_absent()
    results.append(("dependency isolation — gate runs with ALL third-party ABSENT + correctness A/B/C (AIPOS-218 WS6)", ok, detail))
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
