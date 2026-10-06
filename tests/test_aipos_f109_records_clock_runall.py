"""AIPOS-F109 — 记录落点、时间原语与测试登记结构(族 C-c: H10/M12/L1 + gap #40)+ 棘轮基线可并行合并(件⑤)。

验收(卡面 ★验收):
 ① 记录落点单源: transitions.schema record_locations 为唯一声明(节点记录 node_ref 指节点 record.location, 不写第二份);
    config.schema path_conventions / records.subdirs 删除; 产品代码零写死 records/<子目录>(AST 扫描), 一律经
    record_writer.record_dir; 改声明即跟随; 干跑预览路径 == 真写路径(finalization / deployment); 写入器显式 key(卡 ID 含 `_`、
    publish、event 不再落错目录); 存量旧名记录照读(靶场)。
 ② 时间原语: tools/aipos_cli/clock.py 唯一取当前时间(git grep `datetime.now(` 仅此处), ISO 输出与 slug 逐字节同改造前。
 ③ schema 过期叙述清除(grep)。
 ④ run-all 自动发现: 声明行解析 fail-closed; 发现与「本卡测试文件」同一判定; 门 TEST_NOT_IN_RUNALL 在 discover 模式下
    命中式样即登记、exclude 才拒; 两张卡各加测试 → git merge 无冲突(对照: 旧式追加登记冲突); 输出 ✓/✗ 行式样不变,
    known-failure 严格(新增失败红、转绿红)。
 ⑤ 棘轮基线只存条目、一行一条、稳定排序: 两分支各删不同条目 → git merge 无冲突(对照: 旧式带派生计数冲突)。
"""
from __future__ import annotations

import ast
import copy
import io
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ratchet_baseline import canonical_line, read_entries, render_entries  # noqa: E402
from test_aipos_f97_test_file_criterion import _card, _card_branch, _rig, _runall_reasons  # noqa: E402  — 靶场唯一来源

from tools.aipos_cli import clock, runall_discovery  # noqa: E402
from tools.aipos_cli.record_writer import (  # noqa: E402
    build_runtime_id,
    record_dir,
    record_kind_for_type,
    record_location,
    record_root,
    write_records_atomic,
)
from tools.aipos_cli.workspace_config import (  # noqa: E402
    card_test_files,
    default_test_contract,
    discover_test_files,
    runall_directives,
    runall_unregistered,
)

RUNALL = "tests/run-all.sh"


def _show(msg: str) -> None:
    sys.__stdout__.write(msg + "\n")
    sys.__stdout__.flush()


def _schema(name: str) -> dict:
    return json.loads((REPO_ROOT / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


def _g(repo: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=f109", "-c", "user.email=f109@fixture", *argv], cwd=repo,
                          capture_output=True, text=True, check=check)


def _md(fm: dict) -> str:
    from tools.aipos_cli.record_writer import render_markdown

    return render_markdown(fm, "\n# record\n", list(fm))


# ===========================================================================
# ① 记录落点唯一
# ===========================================================================

def test_item1_record_locations_single_declaration():
    config = _schema("config")
    assert "path_conventions" not in config and "n6_closure_checklist" not in config
    assert "subdirs" not in config["governance_structure"]["paths"]["records"]
    kinds = _schema("transitions")["record_locations"]["kinds"]
    seen_types: dict[str, str] = {}
    table = []
    for kind, entry in kinds.items():
        assert bool(entry.get("node_ref")) != bool(entry.get("location")), (kind, entry)  # 节点记录不写第二份模板
        assert entry.get("timestamp_style") in (None, *clock.SLUG_FORMATS), (kind, entry)
        for rtype in entry["record_types"]:
            assert rtype not in seen_types, (rtype, kind, seen_types.get(rtype))
            seen_types[rtype] = kind
            assert record_kind_for_type(rtype) == kind
        location = record_location(kind)
        parts = location.split("/")[:-1]
        static = []
        for part in parts:
            if "{" in part:
                break
            static.append(part)
        assert record_root(kind) == Path(*static) and str(record_root(kind)).startswith("5_tasks/records/"), (kind, location)
        table.append(f"{kind:22s} {location}")
    _show("[①] record_locations(唯一声明)→ record_location:\n" + "\n".join(table))
    # 节点记录读节点 record.location(唯一一份)
    nodes = _schema("transitions")["nodes"]
    assert record_location("claims") == nodes["N1"]["record"]["location"]
    assert record_location("deployments") == nodes["N5"]["deployment_record"]["location"]
    assert record_location("amendments") == nodes["queue_rework"]["record"]["location"]


def test_item1_declaration_drives_paths_and_rejects_unsafe(tmp_path):
    custom = copy.deepcopy(_schema("transitions"))
    custom["nodes"]["N1"]["record"]["location"] = "5_tasks/records/claims_v9/{task_id}/claim_{task_id}_{timestamp}_{agent_instance}.md"
    assert record_dir(tmp_path, "claims", "T-1", custom) == tmp_path / "5_tasks/records/claims_v9/T-1"
    assert record_dir(tmp_path, "claims", "T-1") == tmp_path / "5_tasks/records/claims/T-1"
    assert record_dir(tmp_path, "audit_verdicts") == tmp_path / "5_tasks/records/audit_verdicts"
    for bad in ("../x", "a/b", ""):
        with pytest.raises(ValueError):
            record_dir(tmp_path, "claims", bad)
    with pytest.raises(ValueError, match="平铺"):
        record_dir(tmp_path, "deployments", "T-1")
    with pytest.raises(ValueError, match="未在 transitions.schema record_locations.kinds 声明"):
        record_location("no_such_kind")
    with pytest.raises(ValueError, match="Unknown record_type"):
        record_kind_for_type("no_such_type")


def _record_path_constructions() -> list[tuple[str, int, str]]:
    kinds = set(_schema("transitions")["record_locations"]["kinds"]) | {"records"}
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "tools/aipos_cli", "tools/mcp_server"],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
    hits: list[tuple[str, int, str]] = []
    for rel in out:
        if not rel.endswith(".py") or "/tests/" in rel or rel.rsplit("/", 1)[-1].startswith("test_"):
            continue
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and side.value in kinds:
                        hits.append((rel, node.lineno, side.value))
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Path" and node.args
                    and isinstance(node.args[0], ast.Constant) and "records/" in str(node.args[0].value)):
                hits.append((rel, node.lineno, str(node.args[0].value)))
    return hits


def test_item1_no_hardcoded_record_path_construction_in_product_code():
    hits = _record_path_constructions()
    _show(f"[①] 产品代码记录路径拼接(`/ \"<记录类>\"`、`/ \"records\"`、Path(\"…records/…\"))命中: {hits}")
    assert hits == []


def test_item1_dry_run_path_equals_written_path_and_matches_declaration(tmp_path):
    from tools.aipos_cli.deployment_record import write_deployment_record
    from tools.aipos_cli.finalization_record import write_finalization_record

    kw = dict(governance_root=tmp_path, task_id="AIPOS-X1", actor="exec.lybra.x", commit="a" * 40,
              authorization_type="verdict_ref", authorization_ref="verdict_X", finalized_at="2026-10-06T06:14:45Z")
    dry = write_finalization_record(**kw, dry_run=True)
    real = write_finalization_record(**kw)
    assert Path(dry["path"]) == tmp_path / real["path"]
    assert real["path"] == "5_tasks/records/finalizations/AIPOS-X1/finalization_AIPOS-X1_20261006_061445_exec-lybra-x.md"
    assert real["frontmatter"]["finalize_ref"] == Path(real["path"]).stem
    dkw = dict(governance_root=tmp_path, commit="b" * 40, actor="x", authorization_type="verdict_ref",
               authorization_ref="verdict_X", deployed_at="2026-10-06T06:14:45Z")
    ddry = write_deployment_record(**dkw, dry_run=True)
    dreal = write_deployment_record(**dkw)
    assert Path(ddry["path"]) == tmp_path / dreal["path"]
    assert dreal["path"] == "5_tasks/records/deployments/deployment_20261006_061445_bbbbbbbb.md"
    _show(f"[①] 干跑 == 真写: {real['path']} / {dreal['path']}(无冒号文件名, 同声明模板)")
    for rel, kind in ((real["path"], "finalizations"), (dreal["path"], "deployments")):
        template = record_location(kind)
        pattern = "^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(template)) + "$"
        assert re.match(pattern, rel), (rel, template)


def test_item1_writer_explicit_key_lands_in_declared_dir(tmp_path):
    ts = "2026-10-06T06:14:45Z"
    claim_id = build_runtime_id("claim", "FOO_1", ts, "exec.a")
    publish_id = "publish_aipos-f9"
    written = write_records_atomic(tmp_path, [
        ("claim", claim_id, _md({"record_type": "claim_log", "task_id": "FOO_1"}), "FOO_1"),
        ("publish", publish_id, _md({"record_type": "publish_record", "task_id": "AIPOS-F9"}), "AIPOS-F9"),
        ("event", "started_20261006_061445", _md({"record_type": "task_progress_event", "task_id": "AIPOS-F9"}), "AIPOS-F9"),
    ])
    _show(f"[①] 显式 key 落点: {written['paths']}")
    assert written["paths"] == [
        f"5_tasks/records/claims/FOO_1/{claim_id}.md",          # 卡 ID 含 `_`: 原「record_id 第二段」推导落到 claims/FOO/
        "5_tasks/records/publishes/AIPOS-F9/publish_aipos-f9.md",  # 原落小写 slug 目录, 与 expected_publish_record_path 不一致
        "5_tasks/records/events/AIPOS-F9/started_20261006_061445.md",  # 原落 events/20261006/event_started_…
    ]
    legacy = write_records_atomic(tmp_path, [("return", "return_AIPOS-F9_20261006_061445_exec-a", _md({"record_type": "return_record"}))])
    assert legacy["paths"] == ["5_tasks/records/returns/AIPOS-F9/return_AIPOS-F9_20261006_061445_exec-a.md"]  # 3 元项旧推导不变


def test_item1_legacy_named_records_still_readable(tmp_path):
    """存量兼容靶场: 旧名记录(finalization_<compact> / finalization_<ID>_<ISO 带冒号> / dispatch_<compact_t> /
    publishes/<小写 slug>/ / amendment_<…T…Z>)与新名并存, 读侧照读。"""
    from tools.aipos_cli import next_resolver as nr
    from tools.aipos_cli.board_adapter import read_settlement_status
    from tools.aipos_cli.records import load_records

    gov = tmp_path / "gov"
    (gov / "5_tasks" / "queue" / "completed").mkdir(parents=True)
    rec = gov / "5_tasks" / "records"
    tid = "AIPOS-L1"
    files = {
        rec / "finalizations" / tid / "finalization_20260908_040000.md": {"record_type": "finalization_record", "task_id": tid, "merge_commit": "a" * 40},
        rec / "finalizations" / tid / f"finalization_{tid}_2026-10-06T06:16:51Z.md": {"record_type": "finalization_record", "task_id": tid, "merge_commit": "b" * 40},
        rec / "closures" / tid / f"close_{tid}_20260908_050000_exec-a.md": {"record_type": "closure_record", "task_id": tid, "closed_at": "2026-09-08T05:00:00Z"},
        rec / "audit_dispatches" / f"{tid}R" / "dispatch_20260818T145115.md": {"record_type": "audit_dispatch_record", "dispatch_id": "dispatch_20260818T145115", "reviewed_task_id": tid, "audit_task_id": f"{tid}R", "dispatched_at": "2026-08-18T14:51:15Z"},
        rec / "publishes" / tid.lower() / f"publish_{tid.lower()}.md": {"record_type": "publish_record", "task_id": tid, "publish_id": f"publish_{tid.lower()}", "published_at": "2026-08-01T00:00:00Z"},
        rec / "amendments" / tid / f"amendment_{tid}_20261004T135633Z_advisor-x.md": {"record_type": "amendment_record", "task_id": tid},
        rec / "deployments" / "aaaaaaaa" / "deployment_20260908_040000.md": {"record_type": "deployment_record", "commit": "a" * 40},
    }
    for path, fm in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_md(fm), encoding="utf-8")
    report = load_records(gov)
    assert [r["task_id"] for r in report["publishes"]] == [tid]
    assert [r.get("dispatch_id") for r in report["audit_dispatches"]] == ["dispatch_20260818T145115"]
    assert len(report["closures"]) == 1
    settled = read_settlement_status(tid, gov)
    assert len(settled["finalization_records"]) == 2 and len(settled["closure_records"]) == 1 and settled["unreadable"] == []
    assert any(record_dir(gov, "finalizations", tid).glob("finalization_*.md"))  # 推导核 N5→N6 判据(glob 前缀)
    latest = nr._find_latest_record(record_dir(gov, "audit_dispatches", f"{tid}R"), "dispatch")
    assert latest and latest["dispatch_id"] == "dispatch_20260818T145115"
    _show(f"[①] 存量旧名照读: publishes={len(report['publishes'])} dispatches={len(report['audit_dispatches'])} "
          f"finalizations={len(settled['finalization_records'])} closures={len(settled['closure_records'])}")


# ===========================================================================
# ② 时间原语
# ===========================================================================

def test_item2_current_time_only_in_primitive():
    now_hits = subprocess.run(["git", "grep", "--untracked", "-nE", r"datetime\.(now|utcnow)\(|_dt\.now\(", "--", "tools/aipos_cli", "tools/mcp_server"],
                              cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    now_hits = [h for h in now_hits if "/tests/" not in h.split(":", 1)[0]]
    def_hits = subprocess.run(["git", "grep", "--untracked", "-nE", r"def _?(utc_now|now_iso|utc_now_iso|utc_timestamp|stamp_now|iso_z)\(",
                               "--", "tools/aipos_cli", "tools/mcp_server"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    def_hits = [h for h in def_hits if "/tests/" not in h.split(":", 1)[0]]
    _show("[②] git grep datetime.now( / 时间函数定义:\n" + "\n".join(now_hits + def_hits))
    assert {h.split(":", 1)[0] for h in now_hits} == {"tools/aipos_cli/clock.py"}  # 只在原语模块(含其文档串一处)
    assert {h.split(":", 1)[0] for h in def_hits} <= {"tools/aipos_cli/clock.py"} and len(def_hits) == 2


def test_item2_formats_byte_identical_to_legacy():
    moment = datetime(2026, 10, 6, 6, 14, 45, 123456, tzinfo=timezone.utc)
    legacy_iso = moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    assert clock.iso_z(moment) == legacy_iso == "2026-10-06T06:14:45Z"
    text = "2026-10-06T06:14:45Z"
    assert clock.file_slug("compact", text) == text.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "") == "20261006_061445"
    assert clock.file_slug("compact_t", text) == text.replace(":", "").replace("-", "").replace("Z", "") == "20261006T061445"
    assert clock.file_slug("compact", moment) == moment.strftime("%Y%m%d_%H%M%S")
    assert clock.file_slug("millis", moment) == moment.strftime("%Y%m%d-%H%M%S-%f")[:-3] == "20261006-061445-123"
    assert clock.file_slug("digits", moment) == "20261006061445" and clock.file_slug("date", moment) == "2026-10-06"
    assert clock.file_slug("month", moment) == "2026-10"
    assert build_runtime_id("claim", "AIPOS-1", text, "exec.a") == "claim_AIPOS-1_20261006_061445_exec-a"
    with pytest.raises(ValueError, match="未声明的 slug 式样"):
        clock.file_slug("nope")
    with pytest.raises(ValueError, match="只收 datetime"):
        clock.file_slug("date", text)
    assert clock.utc_now().tzinfo is timezone.utc and clock.iso_z().endswith("Z")


# ===========================================================================
# ③ schema 过期叙述
# ===========================================================================

STALE_PHRASES = ("path_conventions", "n6_closure_checklist", "deleted on finalize", "loop cannot start", "card/<reviewed_task_id>")


def test_item3_stale_schema_narratives_cleared():
    hits = []
    for path in sorted((REPO_ROOT / "schema").glob("*.json")):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {phrase}" for phrase in STALE_PHRASES if phrase in text]
    _show(f"[③] schema 过期叙述 grep {STALE_PHRASES} 命中: {hits}")
    assert hits == []
    card_prefix = _schema("config")["governance_worktree"]["branch_semantics"]["card_prefix"]
    assert "pattern" not in card_prefix and "lifecycle" not in card_prefix and "branch_integration" in card_prefix["description"]


# ===========================================================================
# ④ run-all 自动发现
# ===========================================================================

def test_item4_directives_parse_and_fail_closed():
    text = "#!/bin/bash\n# lybra-runall: discover\n# lybra-runall: exclude tests/test_a.py 理由\n# lybra-runall: known-failure tests/test_b.py::t1\n"
    d = runall_directives(text)
    assert d == {"discover": True, "exclude": {"tests/test_a.py": "理由"}, "known_failures": {"tests/test_b.py::t1": ""}}
    for bad, why in (("# lybra-runall: discovr", "未声明"), ("# lybra-runall: exclude tests/test_a.py", "须写理由"),
                     ("# lybra-runall: known-failure", "缺目标"), ("# lybra-runall: discover now", "不带参数"),
                     ("# lybra-runall: exclude tests/t.py r\n# lybra-runall: known-failure tests/t.py", "重复")):
        with pytest.raises(ValueError, match=why):
            runall_directives(bad)
    assert runall_directives("python3 -m pytest tests/test_x.py\n")["discover"] is False


def test_item4_repo_runall_discovers_with_same_judgment_as_card_test_files():
    runall = (REPO_ROOT / RUNALL).read_text(encoding="utf-8")
    directives = runall_directives(runall)
    assert directives["discover"] is True
    assert all(reason for reason in directives["exclude"].values())
    contract = {**default_test_contract(), "runall_path": RUNALL}
    files = runall_discovery.repo_files(REPO_ROOT)
    discovered = discover_test_files(files, contract)
    assert discovered == sorted(card_test_files([("A", f) for f in files], contract))  # 同一判定 is_test_file
    assert RUNALL not in discovered and "tests/fake_harness.py" not in discovered and "tests/ratchet_baseline.py" not in discovered
    plan = runall_discovery.build_plan(REPO_ROOT, RUNALL, contract)
    assert plan["problems"] == [] and set(plan["to_run"]) == set(discovered) - set(plan["file_excluded"])
    assert all(runall_discovery.runner_for(p) for p in plan["to_run"])
    assert runall_unregistered(["tests/test_aipos_f109_records_clock_runall.py"], runall)[0] == []
    _show(f"[④] 本仓 run-all 自动发现 {len(discovered)} 个测试文件: 执行 {len(plan['to_run'])}, 声明排除 {len(plan['file_excluded'])} 文件"
          f" + {len(plan['node_excluded'])} 节点, 已知存量失败 {len(plan['known_failures'])} 条")


def test_item4_gate_discover_mode_registers_by_glob_and_rejects_excluded(tmp_path, monkeypatch):
    gov, repo = _rig(tmp_path, monkeypatch)
    _card(gov, "DSC-1", "claimed")
    _card_branch(repo, "DSC-1", write={
        RUNALL: "#!/bin/bash\n# lybra-runall: discover\n# lybra-runall: exclude tests/test_skip.py 需外部浏览器\n",
        "tests/test_brand_new.py": "def test_new(): pass\n",
        "tests/test_skip.py": "def test_skip(): pass\n",
    })
    reasons, warnings = _runall_reasons(gov, "DSC-1")
    _show(f"[④] discover 模式门判: reasons={reasons} warnings={warnings}")
    assert len(reasons) == 1 and reasons[0].startswith("TEST_NOT_IN_RUNALL") and "tests/test_skip.py" in reasons[0]
    assert "tests/test_brand_new.py" not in reasons[0] and "exclude" in reasons[0]
    _card(gov, "DSC-2", "claimed")
    _card_branch(repo, "DSC-2", write={RUNALL: "#!/bin/bash\n# lybra-runall: discover\n", "tests/test_other_new.py": "def test_o(): pass\n"})
    assert _runall_reasons(gov, "DSC-2") == ([], [])
    _card(gov, "DSC-3", "claimed")
    _card_branch(repo, "DSC-3", write={RUNALL: "#!/bin/bash\n# lybra-runall: bogus\n", "tests/test_third.py": "def test_t(): pass\n"})
    bad, _w = _runall_reasons(gov, "DSC-3")
    assert len(bad) == 1 and bad[0].startswith("RUNALL_DIRECTIVE_INVALID")


def test_item4_two_cards_add_tests_merge_without_conflict(tmp_path):
    """两张卡各加测试: 新式(自动发现)不改清单 → git merge 零冲突、合并树发现两者; 对照: 旧式往清单同处追加登记 → 冲突。"""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _g(repo, "init", "-q", "-b", "main")
    shutil.copy(REPO_ROOT / RUNALL, repo / RUNALL)
    (repo / "tests" / "test_base.py").write_text("def test_base(): pass\n", encoding="utf-8")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "base")
    runall_before = (repo / RUNALL).read_text(encoding="utf-8")
    for branch, name in (("card/X", "test_x_alpha.py"), ("card/Y", "test_y_beta.py")):
        _g(repo, "checkout", "-q", "-b", branch, "main")
        (repo / "tests" / name).write_text("def test_it(): pass\n", encoding="utf-8")
        _g(repo, "add", "-A")
        _g(repo, "commit", "-q", "-m", f"{branch} 加测试")
    _g(repo, "checkout", "-q", "main")
    _g(repo, "merge", "-q", "--no-ff", "-m", "Merge card/X", "card/X")
    merged = _g(repo, "merge", "--no-ff", "-m", "Merge card/Y", "card/Y", check=False)
    status = _g(repo, "status", "--porcelain").stdout.strip()
    _show(f"[④] 自动发现靶场 git merge card/Y: rc={merged.returncode} {merged.stdout.strip()!r}; status={status!r}")
    assert merged.returncode == 0 and status == "" and (repo / RUNALL).read_text(encoding="utf-8") == runall_before
    contract = {**default_test_contract(), "runall_path": RUNALL}
    files = runall_discovery.repo_files(repo)
    plan_files = discover_test_files(files, contract)
    assert {"tests/test_x_alpha.py", "tests/test_y_beta.py", "tests/test_base.py"} <= set(plan_files)
    assert runall_unregistered(["tests/test_x_alpha.py", "tests/test_y_beta.py"], runall_before)[0] == []

    control = tmp_path / "control"
    (control / "tests").mkdir(parents=True)
    _g(control, "init", "-q", "-b", "main")
    (control / RUNALL).write_text("#!/bin/bash\npython3 -m pytest tests/test_base.py\n\necho done\n", encoding="utf-8")
    _g(control, "add", "-A")
    _g(control, "commit", "-q", "-m", "base")
    for branch, name in (("card/X", "test_x_alpha.py"), ("card/Y", "test_y_beta.py")):
        _g(control, "checkout", "-q", "-b", branch, "main")
        text = (control / RUNALL).read_text(encoding="utf-8")
        (control / RUNALL).write_text(text.replace("\necho done\n", f"python3 -m pytest tests/{name}\n\necho done\n"), encoding="utf-8")
        _g(control, "commit", "-q", "-am", f"{branch} 登记")
    _g(control, "checkout", "-q", "main")
    _g(control, "merge", "-q", "--no-ff", "-m", "Merge card/X", "card/X")
    conflict = _g(control, "merge", "--no-ff", "-m", "Merge card/Y", "card/Y", check=False)
    _show(f"[④] 对照(旧式追加登记)git merge card/Y: rc={conflict.returncode} {conflict.stdout.strip()!r}")
    assert conflict.returncode != 0 and "CONFLICT" in conflict.stdout


def test_item4_runner_output_format_and_known_failure_is_strict(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _g(repo, "init", "-q", "-b", "main")
    (repo / RUNALL).write_text(
        "#!/bin/bash\n# lybra-runall: discover\n# lybra-runall: known-failure tests/test_kf.py::test_known 存量\n"
        "# lybra-runall: exclude tests/test_ex.py 夹具环境不可执行\n", encoding="utf-8")
    (repo / "tests" / "test_ok.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_kf.py").write_text("def test_known(): assert False\n\ndef test_fine(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_ex.py").write_text("def test_ex(): assert False\n", encoding="utf-8")
    (repo / "tests" / "test_script.py").write_text("print('script-style ok')\n", encoding="utf-8")
    contract = {**default_test_contract(), "runall_path": RUNALL}
    out = io.StringIO()
    rc = runall_discovery.run(repo, RUNALL, contract, out=out)
    text = out.getvalue()
    _show("[④] 执行器输出(✓/✗ 行):\n" + "\n".join(ln for ln in text.splitlines() if (ln and ln[0] in "✓✗⊘") or ln.startswith("[runall_discovery]")))
    assert rc == 0
    for path in ("tests/test_ok.py", "tests/test_kf.py", "tests/test_script.py"):
        assert f"── {path} " in text and re.search(rf"^✓ {re.escape(path)} PASS$", text, re.M), path
    assert "⊘ 未执行(声明 exclude): tests/test_ex.py —— 夹具环境不可执行" in text and "── tests/test_ex.py " not in text
    assert "脚本式夹具" in text
    # 严格: 新增失败 = 红; 声明了却转绿 = 红; 整文件声明转绿 = 红
    out_fail = "FAILED tests/test_kf.py::test_known - assert False\nFAILED tests/test_kf.py::test_fine - boom\n"
    ok, notes = runall_discovery.judge("tests/test_kf.py", "pytest", 1, out_fail, {"tests/test_kf.py::test_known": ""})
    assert not ok and any("新增失败" in n and "test_fine" in n for n in notes)
    ok, notes = runall_discovery.judge("tests/test_kf.py", "pytest", 0, "", {"tests/test_kf.py::test_known": ""})
    assert not ok and any("须从清单删此行" in n for n in notes)
    ok, notes = runall_discovery.judge("tests/x.test.js", "node", 0, "", {"tests/x.test.js": ""})
    assert not ok and any("已转绿" in n for n in notes)
    ok, _n = runall_discovery.judge("tests/x.test.js", "node", 1, "", {"tests/x.test.js": ""})
    assert ok
    ok, notes = runall_discovery.judge("tests/test_kf.py", "pytest", None, "", {})
    assert not ok and notes == ["超时"]


# ===========================================================================
# ⑤ 棘轮基线可并行合并
# ===========================================================================

def test_item5_baselines_are_entries_only_one_per_line_sorted():
    for name in ("f87_fragmentation_baseline.jsonl", "f96_docs_retired_practice_baseline.jsonl"):
        path = REPO_ROOT / "tests" / name
        entries = read_entries(path)  # 形坏(非规范/未排序/空行)即抛
        assert path.read_text(encoding="utf-8") == render_entries(entries)
        for key in ("total", "count", "baseline_commit_base", "invariants", "description"):
            assert all(key not in entry for entry in entries), (name, key)
        _show(f"[⑤] {name}: {len(entries)} 条(一行一条, 计数运行时派生)")
    assert not (REPO_ROOT / "tests" / "f87_fragmentation_baseline.json").exists()
    assert not (REPO_ROOT / "tests" / "f96_docs_retired_practice_baseline.json").exists()


def test_item5_two_branches_delete_different_entries_merge_clean(tmp_path):
    entries = [{"file": f"tools/m{i}.py", "fp": f"{i:012x}", "inv": "a", "item": "H5", "text": f"x{i}"} for i in range(8)]
    repo = tmp_path / "jsonl"
    repo.mkdir()
    _g(repo, "init", "-q", "-b", "main")
    (repo / "b.jsonl").write_text(render_entries(entries), encoding="utf-8")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "base")
    for branch, drop in (("card/A", 1), ("card/B", 5)):
        _g(repo, "checkout", "-q", "-b", branch, "main")
        (repo / "b.jsonl").write_text(render_entries([e for i, e in enumerate(entries) if i != drop]), encoding="utf-8")
        _g(repo, "commit", "-q", "-am", f"{branch} 删条目 {drop}")
    _g(repo, "checkout", "-q", "main")
    _g(repo, "merge", "-q", "--no-ff", "-m", "Merge A", "card/A")
    merged = _g(repo, "merge", "--no-ff", "-m", "Merge B", "card/B", check=False)
    _show(f"[⑤] jsonl 基线两分支各删一条 git merge: rc={merged.returncode} {merged.stdout.strip()!r}")
    assert merged.returncode == 0
    assert read_entries(repo / "b.jsonl") == [e for i, e in enumerate(entries) if i not in (1, 5)]

    # 对照: 旧式(派生 total/count + baseline_commit_base 叙述)同样各删一条 → 派生字段同键异值冲突
    control = tmp_path / "json"
    control.mkdir()
    _g(control, "init", "-q", "-b", "main")

    def old_style(kept: list[dict], note: str) -> str:
        return json.dumps({"baseline_commit_base": note, "total": len(kept), "invariants": {"a": {"count": len(kept), "entries": kept}}},
                          ensure_ascii=False, indent=1) + "\n"

    (control / "b.json").write_text(old_style(entries, "base"), encoding="utf-8")
    _g(control, "add", "-A")
    _g(control, "commit", "-q", "-m", "base")
    for branch, drop in (("card/A", 1), ("card/B", 5)):
        _g(control, "checkout", "-q", "-b", branch, "main")
        (control / "b.json").write_text(old_style([e for i, e in enumerate(entries) if i != drop], f"base; {branch} 删 {drop}"), encoding="utf-8")
        _g(control, "commit", "-q", "-am", f"{branch} 删条目 {drop}")
    _g(control, "checkout", "-q", "main")
    _g(control, "merge", "-q", "--no-ff", "-m", "Merge A", "card/A")
    conflict = _g(control, "merge", "--no-ff", "-m", "Merge B", "card/B", check=False)
    _show(f"[⑤] 对照(旧式带派生计数)git merge: rc={conflict.returncode} {conflict.stdout.strip()!r}")
    assert conflict.returncode != 0 and "CONFLICT" in conflict.stdout
    assert canonical_line(entries[0]).startswith('{"file": ')
