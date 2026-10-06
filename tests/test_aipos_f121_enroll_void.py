"""AIPOS-F121 件② — 接入日志测试行作废: `lybra roles enroll-void --instance <实例> --reason <理由> --actor <操作者> [--dry-run]`。

依据(gap #34/#70, F116 发现): tools/test_aipos_r2_enroll.py 原经生产门往真实 lybra governance/enrollment_log.md 写
instance=test.enroll.aipos-r2 的 create/use/land 行(10-06 已 238 行)。日志是 append-only 审计真相, 不许删行;
清理 = 追加 void 事件行(指向被作废的行号区间与 code_id, 带操作者与理由), 读取口(workstation_location / enroll-where)据此忽略。

靶场: tmp home(LYBRA_HOME_ROOT/HOME 指向 tmp)下两个已建项目 proj-a(签发门)/proj-b(禁真治理根/真工位/真门)。
验收:
 ① dry-run 零写入(日志逐字节不变), 打印将追加的 void 行
 ② 执行 = 只追加: 原内容是新内容的前缀, 恰多 1 行; 行含 voids_lines 区间 / voids_code_ids / by= / reason=
 ③ 读取口忽略已作废事件: workstation_location found=False, enroll-where actions 只计未作废事件; 他实例不受影响
 ④ 作废后重接入 = 新事件 live(区间外), 工位可再定位
 ⑤ 拒: 未知实例 / 已全部作废 / 缺操作者或理由(零写入)
 ⑥ 存量写在签发方 log 的事件就地作废(定位与 enroll-where 同一扫描)
 ⑦ 坏 void 行 fail-closed(ValueError, 不当作未作废静默放过)
 ⑧ 单实现: 行渲染/写口/事件分拣各一处; 新函数无 except Exception
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli import enrollment  # noqa: E402


def _show(text: str) -> None:
    print(text, flush=True)


def _project(home: Path, name: str) -> Path:
    from tools.aipos_cli.workspace_config import write_project_json

    root = home / name
    (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    write_project_json(root, name, code_repo=str(home / f"{name}-code"))
    return root


@pytest.fixture
def two(tmp_path, monkeypatch):
    home = tmp_path / "projects-home"
    a, b = _project(home, "proj-a"), _project(home, "proj-b")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LYBRA_HOME_ROOT", str(home))
    ws = tmp_path / "workstations" / "b-exec"
    ws.mkdir(parents=True)
    return type("Two", (), {"home": home, "a": a, "b": b, "ws": ws, "tmp": tmp_path})


def _log(root: Path) -> Path:
    return enrollment.enrollment_trail_path(root)


def _enroll(two, inst: str, ws: Path) -> str:
    """A 签发 B 的实例码 → create/use/land 落 B 的 log(产品 API, 与 F107 夹具同形); 返回 code_id。"""
    issued = enrollment.issue_self_contained_code(two.a, role="executor", instance=inst, ttl_seconds=600,
                                                  governance_root=str(two.b), by="fixture-owner-ref", reason="夹具接入")
    inner = enrollment.decode_self_contained_code(issued["self_contained_code"])["code"]
    enrollment.mark_enrollment_used(two.a, inner, token_entry={"role": "executor", "token": "fixture-not-a-secret",
                                                              "projects": ["proj-b"], "agent_instance": inst})
    enrollment.land_enrollment(two.a, inner, landed_detail=f"host={socket.gethostname()} workstation={ws} files=['connection.json', 'role']")
    return issued["code_id"]


def _cli(two, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env.update({"PYTHONPATH": str(REPO_ROOT), "HOME": str(two.tmp), "LYBRA_HOME_ROOT": str(two.home)})
    return subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", "--workspace-root", str(two.a), "roles", *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT), env=env, timeout=120)


def _void(two, inst: str, *extra: str) -> subprocess.CompletedProcess:
    return _cli(two, "enroll-void", "--instance", inst, "--reason", "测试行作废(F121 夹具)", "--actor", "advisor.fixture", *extra)


def _digest(*roots: Path) -> dict[str, str]:
    return {str(_log(r)): hashlib.sha256(_log(r).read_bytes()).hexdigest() if _log(r).is_file() else "-" for r in roots}


def _numbered(path: Path, inst: str) -> list[int]:
    return [n for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if ln.startswith("- ") and f"instance={inst}" in ln and "  void  " not in ln]


def test_void_dry_run_then_append_only_and_readers_ignore(two):
    inst, other = "exec.proj-b.hostx", "exec.proj-b.hosty"
    code_1 = _enroll(two, inst, two.ws)
    code_2 = _enroll(two, inst, two.ws)
    _enroll(two, other, two.ws)
    log_b = _log(two.b)
    assert enrollment.workstation_location(two.b, inst)["found"] is True
    lines = _numbered(log_b, inst)
    assert len(lines) == 6

    # ① dry-run 零写入
    before = _digest(two.a, two.b)
    dry = _void(two, inst, "--dry-run")
    _show(f"---- ① lybra roles enroll-void --dry-run 原文 ----\n{dry.stdout}{dry.stderr}")
    assert dry.returncode == 0, dry.stderr
    assert _digest(two.a, two.b) == before
    assert "dry-run(零写入)" in dry.stdout and "  void  " in dry.stdout

    # ② 执行 = 只追加一行
    text_before = log_b.read_text(encoding="utf-8")
    run = _void(two, inst, "--json")
    assert run.returncode == 0, run.stderr
    rep = json.loads(run.stdout)
    _show(f"---- ② enroll-void 执行(--json)原文 ----\n{run.stdout}")
    text_after = log_b.read_text(encoding="utf-8")
    _show(f"---- ② B 的 enrollment_log 执行后原文 ----\n{text_after}")
    assert text_after.startswith(text_before) and text_after.count("\n") == text_before.count("\n") + 1
    void_line = text_after.splitlines()[-1]
    expected_ranges = enrollment._line_ranges(lines)
    assert rep["written"] is True and rep["entries"][0]["voids_lines"] == expected_ranges
    assert void_line == rep["entries"][0]["line"]
    for needle in ("  void  ", f"instance={inst}", "project=proj-b", f"voids_lines={expected_ranges}",
                   "by=advisor.fixture", "reason=测试行作废(F121 夹具)", code_1, code_2):
        assert needle in void_line, needle

    # ③ 读取口忽略已作废事件; 他实例不受影响
    loc = enrollment.workstation_location(two.b, inst)
    _show(f"[③] workstation_location(B, {inst}) 作废后 = {loc}")
    assert loc["found"] is False
    assert enrollment.workstation_location(two.b, other)["found"] is True
    where = json.loads(_cli(two, "enroll-where", "--instance", inst, "--json").stdout)
    human = _cli(two, "enroll-where", "--instance", inst)
    _show(f"---- ③ enroll-where 作废后原文 ----\n{human.stdout}")
    item = where["found"][0]
    assert item["actions"] == {} and item["voided_events"] == 6 and item["live_lines"] == ""
    assert where["verdict"] == "not_landed"

    # ⑤ 已全部作废 → 拒, 零写入
    again = _void(two, inst)
    _show(f"[⑤ 重复作废] exit={again.returncode} {again.stderr.strip()}")
    assert again.returncode != 0 and "已全部作废" in again.stderr
    assert log_b.read_text(encoding="utf-8") == text_after

    # ④ 作废后重接入 → 新事件 live, 工位可再定位
    _enroll(two, inst, two.ws)
    assert enrollment.workstation_location(two.b, inst)["found"] is True
    where = enrollment.enrollment_whereabouts(two.a, inst)
    assert where["verdict"] == "in_place" and where["found"][0]["actions"] == {"create": 1, "use": 1, "land": 1}
    assert where["found"][0]["voided_events"] == 6


def test_void_rejections_write_nothing(two):
    _enroll(two, "exec.proj-b.hostx", two.ws)
    before = _digest(two.a, two.b)
    unknown = _void(two, "exec.proj-b.nobody")
    _show(f"[⑤ 未知实例] exit={unknown.returncode} {unknown.stderr.strip()}")
    assert unknown.returncode != 0 and "未知实例" in unknown.stderr
    no_actor = _cli(two, "enroll-void", "--instance", "exec.proj-b.hostx", "--reason", "x")
    no_reason = _cli(two, "enroll-void", "--instance", "exec.proj-b.hostx", "--actor", "x")
    assert no_actor.returncode != 0 and "--actor" in no_actor.stderr
    assert no_reason.returncode != 0 and "--reason" in no_reason.stderr
    with pytest.raises(ValueError, match="换行"):
        enrollment.void_instance_events(two.a, "exec.proj-b.hostx", by="x", reason="a\nb", dry_run=False)
    with pytest.raises(ValueError, match="空白"):
        enrollment.void_instance_events(two.a, "exec.proj-b.hostx", by="a b", reason="r", dry_run=False)
    assert _digest(two.a, two.b) == before


def test_void_legacy_events_in_issuer_log_in_place(two):
    """⑥ 存量事件写在签发方 log(F107 前写侧, 无 project= 字段)→ 就地在该 log 作废; 读取口同样忽略。"""
    inst = "hbj-coder.proj-b.hostx"
    legacy = _log(two.a)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# Enrollment Codes Log (append-only)\n\n"
                      f"- 2026-08-28T14:50:26Z  create  code_id=enroll_legacy  role=hbj-coder  instance={inst}  by=x  reason=t\n"
                      f"- 2026-08-28T14:50:26Z  land  code_id=enroll_legacy  role=hbj-coder  instance={inst}  by=(agent-enroll)  "
                      f"reason=workstation={two.ws} files=['connection.json', 'role']\n", encoding="utf-8")
    assert enrollment.workstation_location(two.a, inst)["found"] is True
    rep = enrollment.void_instance_events(two.a, inst, by="advisor.fixture", reason="存量测试行", dry_run=False)
    assert [e["log"] for e in rep["entries"]] == [str(legacy)] and rep["entries"][0]["voids_lines"] == "3-4"
    assert "project=proj-a" in rep["entries"][0]["line"] and "role=hbj-coder" in rep["entries"][0]["line"]
    assert enrollment.workstation_location(two.a, inst)["found"] is False
    assert enrollment.enrollment_whereabouts(two.a, inst)["found"][0]["voided_events"] == 2


def test_malformed_void_line_fails_closed(two):
    """⑦ 坏 voids_lines 不得被当作「未作废」静默放过。"""
    inst = "exec.proj-b.hostx"
    _enroll(two, inst, two.ws)
    with _log(two.b).open("a", encoding="utf-8") as fh:
        fh.write(f"- 2026-10-06T00:00:00Z  void  code_id=-  role=executor  instance={inst}  project=proj-b  "
                 "voids_lines=9-x  voids_code_ids=-  by=x  reason=坏行\n")
    with pytest.raises(ValueError, match="voids_lines"):
        enrollment.workstation_location(two.b, inst)


def test_single_implementation_and_fail_closed():
    """⑧ 行渲染 / 写口 / 事件分拣各一处; 读取口经 _instance_events; 新段无 except Exception。"""
    src = (REPO_ROOT / "tools" / "aipos_cli" / "enrollment.py").read_text(encoding="utf-8")
    assert src.count("def _trail_line(") == 1 and src.count("def _write_trail_line(") == 1
    assert src.count('.open("a"') == 1  # 接入日志唯一追加写口
    assert src.count("def _instance_events(") == 1 and src.count("_VOID_LINE_RE.match(") == 1
    assert src.count("owner = enrollment_owner_root(") == 2  # F107 不变量: 写侧 + 诊断同一解析口(void 复用诊断扫描, 不另解析)
    for fn in (enrollment._instance_events, enrollment._latest_land, enrollment.enrollment_whereabouts,
               enrollment.void_instance_events, enrollment._trail_line, enrollment._write_trail_line,
               enrollment._parse_line_ranges, enrollment._line_ranges):
        body = inspect.getsource(fn)
        assert "except Exception" not in body and "pass\n" not in body, fn.__name__
    for fn in (enrollment._latest_land, enrollment.enrollment_whereabouts, enrollment.void_instance_events):
        assert "_instance_events(" in inspect.getsource(fn), fn.__name__
    cli = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    assert cli.count('add_parser("enroll-void"') == 1 and "void_instance_events(" in cli


def test_line_ranges_roundtrip():
    nums = [3, 4, 5, 9, 11, 12, 30]
    text = enrollment._line_ranges(nums)
    assert text == "3-5,9,11-12,30"
    assert enrollment._parse_line_ranges(text, where="t") == set(nums)
    assert enrollment._line_ranges([]) == ""
