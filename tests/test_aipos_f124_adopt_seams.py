"""AIPOS-F124 — 接入既有项目③收尾: F122/F123 合入后的四处接缝。

靶场(禁真门/真治理根/pi/chris 治理根): 收编靶场 = AIPOS-F123 夹具(tmp 治理根 + tmp 产品仓 + tmp HOME + 进程内门, 唯一来源
test_aipos_f90_loop_one_stage / test_aipos_f123_adopt_and_set_paths); 守卫靶场 = AIPOS-F116 夹具(假「真实」home 根 git 仓 +
假 ~/.lybra/config.json, 唯一来源 test_aipos_f116_test_hygiene)。人肉期样本全部临时自造。

件① adopt 对冻结卡拒: queue_mutation.legacy_frozen_refusal 接通唯一判定 legacy_baseline.frozen_rejection(原恒 None 接口),
     拒因 ADOPT_LEGACY_FROZEN + LEGACY_FROZEN 附解冻命令; 解冻后可收编; 清单读不出 = 拒(fail-closed)。
件② 接入顺序 set-paths → freeze-legacy → adopt(onboarding 一节各附原因); 收编依赖的落点(config.schema
     project_json.paths.adoption.required_declared_keys)未显式声明 = 拒 ADOPT_PATHS_UNDECLARED(零写入), 声明后骨架落声明位。
件③ 记录类型单源: legacy_baseline 进 enums.schema record_type; config.schema legacy_baseline.entry 不再另声明 record_type;
     写侧(freeze-legacy)/ 读侧(frozen_tasks → state lint · next · 门 · loop)同读 RecordType.LEGACY_BASELINE; 清单条目过入库护栏 B④。
件④ main 上 f107 回归「持续红」查因: f107 本身在干净克隆与 main 检出目录均 9 passed、守卫下对真实治理根零写入; 红因 = run-all
     真实治理根守卫的时间窗归因把 loop 在合并后异步回归期间继续推进的结案写入(移卡 + closure 记录)算到正在复核重跑的 f107 头上。
     修: runall_discovery.attribute_rerun —— 重跑变动须落在首轮同一落点才算复现。

跑法: python3 -m pytest tests/test_aipos_f124_adopt_seams.py -q -s
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f90_loop_one_stage import _card, _records, rig  # noqa: E402,F401  — 靶场唯一来源
from test_aipos_f123_adopt_and_set_paths import (  # noqa: E402  — 收编靶场 helper 唯一来源
    LEGACY_BRANCH,
    LEGACY_RUNTIME,
    TASK,
    _adopt,
    _cli,
    _legacy_branch,
    _legacy_card,
    _set_paths,
    _snapshot,
)
from test_aipos_f116_test_hygiene import _fake_real_home, _product_repo, _run_executor, _verdict  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402

FREEZER = "advisor.sample"


def _show(text: str) -> None:
    print(text, flush=True)


def _freeze(rig, *args: str) -> tuple[int, str, str]:
    return _cli(["project", "freeze-legacy", "--workspace-root", str(rig.gov), *args, "--reason", "人肉期存量",
                 "--actor", FREEZER, "--confirm"])


def _fn(path: str, name: str) -> ast.FunctionDef:
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _calls(fn: ast.FunctionDef) -> set[str]:
    return {c.func.id if isinstance(c.func, ast.Name) else c.func.attr for c in ast.walk(fn)
            if isinstance(c, ast.Call) and isinstance(c.func, (ast.Name, ast.Attribute))}


# ===========================================================================
# 件① 冻结卡 adopt 拒 → 解冻后收编
# ===========================================================================

def test_item1_frozen_card_adopt_refused_then_unfrozen_adopts(rig):
    _legacy_card(rig)
    _legacy_branch(rig.code_repo)
    rc, out, err = _freeze(rig, "--task-ids", TASK)
    _show(f"[件①·冻结] rc={rc}\n{out}{err}")
    assert rc == 0, out + err

    snap = _snapshot(rig)
    for confirm in (False, True):
        rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=confirm)
        _show(f"[件①·冻结卡 adopt {'--confirm' if confirm else '--dry-run'} 拒] rc={rc}\n{out}{err}")
        assert rc == 1, out + err
        assert "ADOPT_LEGACY_FROZEN: 该卡已冻结为历史(迁移基线), 冻结卡不收编" in out
        assert f"LEGACY_FROZEN: 卡 {TASK} 在存量冻结清单内" in out and "拒 adopt" in out
        assert f"--unfreeze {TASK}" in out, "拒因附解冻命令"
        assert _snapshot(rig) == snap, "冻结卡收编被拒 = 零写入"

    rc, out, err = _freeze(rig, "--unfreeze", TASK)
    _show(f"[件①·解冻] rc={rc}\n{out}{err}")
    assert rc == 0, out + err
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件①·解冻后 adopt --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "已收编" in out, out + err
    claims = _records(rig.gov, "claims", TASK)
    assert len(claims) == 1 and nr._read_frontmatter(claims[0])["adopted_from"] == "legacy_manual"


def test_item1_unreadable_manifest_refuses_adopt_fail_closed(rig):
    _legacy_card(rig)
    _legacy_branch(rig.code_repo)
    assert _freeze(rig, "--task-ids", TASK)[0] == 0
    manifest = next((rig.gov / "5_tasks" / "records" / "legacy_baseline").glob("*.md"))
    (manifest.parent / "legacy_freeze_broken.md").write_text("no frontmatter\n", encoding="utf-8")
    snap = _snapshot(rig)
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件①·清单读不出 adopt 拒] rc={rc}\n{out}{err}")
    assert rc == 1 and "LEGACY_BASELINE_INVALID" in out and "拒 adopt" in out
    assert _snapshot(rig) == snap


def test_item1_single_frozen_judgment_reused():
    """legacy_frozen_refusal 只调唯一判定 frozen_rejection(不读清单 / 不调 frozen_tasks / 无第二判定); adopt 调它。"""
    fn = _fn("tools/aipos_cli/queue_mutation.py", "legacy_frozen_refusal")
    called = _calls(fn)
    assert "frozen_rejection" in called and not {"frozen_tasks", "read_manifest", "manifest_location"} & called, called
    assert "legacy_frozen_refusal" in _calls(_fn("tools/aipos_cli/queue_mutation.py", "adopt_queue_task"))


# ===========================================================================
# 件② 接入顺序 + 落点未声明 adopt 拒
# ===========================================================================

def test_item2_onboarding_order_set_paths_then_freeze_then_adopt_with_reasons(tmp_path):
    from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide

    guide = generate_onboarding_guide("probe_proj", home_root=str(tmp_path), code_repo=str(tmp_path / "repo"), host_segment="h")
    text = format_guide_text(guide)
    section = text[text.index("═══ 附: 接入既有人肉项目"):]
    _show("[件②·接入向导节(改后原文)]\n" + section)
    legacy = guide["legacy_onboarding"]
    verbs = [next(v for v in ("set-paths", "freeze-legacy", "queue adopt") if v in item["command"]) for item in legacy]
    assert verbs == ["set-paths", "freeze-legacy", "queue adopt"] and [i["order"] for i in legacy] == [1, 2, 3]
    assert "顺序 = 落点声明 → 冻结 → 收编" in section
    assert all(item["purpose"].startswith("为什么") for item in legacy), "每步写一句原因"
    assert "return_root" in legacy[0]["purpose"] and "ADOPT_PATHS_UNDECLARED" in legacy[0]["purpose"]
    assert "ADOPT_LEGACY_FROZEN" in legacy[1]["purpose"]


def test_item2_adopt_refused_when_paths_undeclared_then_skeleton_lands_at_declared_root(rig):
    _card(rig.gov, TASK, "claimed", **LEGACY_RUNTIME)  # 人肉期在途卡, project.json 无 paths 段(未 set-paths)
    _legacy_branch(rig.code_repo)
    pj = rig.gov / "project.json"
    assert "paths" not in json.loads(pj.read_text(encoding="utf-8"))
    pj_before = pj.read_bytes()
    snap = _snapshot(rig)
    for confirm in (False, True):
        rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=confirm)
        _show(f"[件②·落点未声明 adopt {'--confirm' if confirm else '--dry-run'} 拒] rc={rc}\n{out}{err}")
        assert rc == 1, out + err
        assert "ADOPT_PATHS_UNDECLARED: 收编依赖的落点未在 project.json paths 显式声明" in out
        assert "paths 未声明 return_root" in out and "lybra project set-paths" in out
        assert _snapshot(rig) == snap and pj.read_bytes() == pj_before, "拒 = 零写入"
    assert not (rig.gov / "task_cards" / TASK).exists(), "未声明时不在缺省位落骨架"

    # 人肉期项目真实落点 ≠ 缺省(chris 形): set-paths 声明后收编, 骨架落声明位
    rc, out, err = _set_paths(rig, [("return_root", "5_tasks/records/returns")], confirm=True)
    assert rc == 0, out + err
    rc, out, err = _adopt(TASK, LEGACY_BRANCH, confirm=True)
    _show(f"[件②·set-paths 后 adopt --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "已收编" in out, out + err
    skeleton = nr.card_report_path(rig.gov, TASK)
    assert skeleton == rig.gov / "5_tasks" / "records" / "returns" / TASK / skeleton.name and skeleton.is_file()
    assert not (rig.gov / "task_cards" / TASK).exists()


def test_item2_paths_requirement_declared_and_fail_closed(monkeypatch):
    from tools.aipos_cli import workspace_config as wc
    from tools.schema_loader import SchemaLoadError

    decl = wc.adoption_paths_declaration()
    assert decl["required_declared_keys"] == ["return_root"]
    assert set(decl["required_declared_keys"]) <= set(wc._project_paths_declaration())
    fn = _fn("tools/aipos_cli/queue_mutation.py", "adoption_paths_refusal")
    assert {"adoption_paths_declaration", "project_paths", "adoption_refusal"} <= _calls(fn), "判定读声明 + 唯一读取口 + 同一拒因格式"
    import tools.schema_loader as sl  # 声明读取口在函数内 from tools.schema_loader import load_schema → 打桩模块属性即生效

    original = sl.load_schema

    def _no_adoption(name, *a, **k):
        data = json.loads(json.dumps(original(name, *a, **k)))
        if name == "config":
            data["configuration_sources"]["project_json"]["schema"]["paths"].pop("adoption")
        return data

    monkeypatch.setattr(sl, "load_schema", _no_adoption)
    with pytest.raises(SchemaLoadError, match="paths.adoption"):
        wc.adoption_paths_declaration()


# ===========================================================================
# 件③ 记录类型单源: enums.schema record_type
# ===========================================================================

def test_item3_legacy_baseline_record_type_single_source(rig):
    from tools.aipos_cli.governance_guardrails import check_entries, load_guardrail_declarations
    from tools.aipos_cli.legacy_baseline import frozen_tasks, record_type
    from tools.schema_constants import RecordType
    from tools.schema_loader import get_enum_values

    enums = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))
    values = [v["value"] for v in enums["enums"]["record_type"]["values"]]
    config = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    entry = config["configuration_sources"]["project_json"]["schema"]["legacy_baseline"]["entry"]
    _show(f"[件③·enums record_type 含 legacy_baseline] {'legacy_baseline' in values}; config entry 键 = {sorted(entry)}")
    assert values.count("legacy_baseline") == 1 and "legacy_baseline" in get_enum_values("record_type")
    assert "record_type" not in entry, "config.schema 不再另声明一份"
    assert RecordType.LEGACY_BASELINE == record_type() == "legacy_baseline"
    for schema in sorted((REPO_ROOT / "schema").glob("*.json")):  # 全 schema 只有枚举一处声明该值
        text = schema.read_text(encoding="utf-8")
        assert '"record_type": "legacy_baseline"' not in text, schema.name

    # 写侧(freeze-legacy)落的条目 record_type ∈ 枚举, 过入库护栏 B④; 读侧(lint/next/门/loop 共用 frozen_tasks)读回
    _legacy_card(rig)
    rc, out, err = _freeze(rig, "--task-ids", TASK)
    assert rc == 0, out + err
    entry_file = next((rig.gov / "5_tasks" / "records" / "legacy_baseline").glob("*.md"))
    meta = nr._read_frontmatter(entry_file)
    rel = entry_file.relative_to(rig.gov).as_posix()
    report = check_entries(rig.gov, [("A", rel)], load_guardrail_declarations(REPO_ROOT / "schema"), current_branch="main")
    _show(f"[件③·清单条目过护栏] {rel} record_type={meta['record_type']} guardrail_ok={report.ok} {report.to_dict()}")
    assert meta["record_type"] in get_enum_values("record_type") and report.ok, report.to_dict()
    assert TASK in frozen_tasks(rig.gov)

    # 写侧 / 读侧都经 record_type()(枚举常量), 代码无字面量 / 无 entry.record_type 读法
    src = (REPO_ROOT / "tools/aipos_cli/legacy_baseline.py").read_text(encoding="utf-8")
    assert 'entry_decl["record_type"]' not in src and 'entry.get("record_type")' not in src
    for name in ("record_type", "_read_entry", "write_freeze_entry"):
        fn = _fn("tools/aipos_cli/legacy_baseline.py", name)
        literals = {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
        assert "legacy_baseline" not in literals, f"{name} 写死记录类型字面量"
    assert "record_type" in _calls(_fn("tools/aipos_cli/legacy_baseline.py", "_read_entry"))
    assert "record_type" in _calls(_fn("tools/aipos_cli/legacy_baseline.py", "write_freeze_entry"))


def test_item3_enum_missing_value_fails_closed(monkeypatch):
    from tools import schema_constants
    from tools.aipos_cli.legacy_baseline import record_type
    from tools.schema_loader import SchemaLoadError

    monkeypatch.delattr(schema_constants.RecordType, "LEGACY_BASELINE")
    with pytest.raises(SchemaLoadError, match="legacy_baseline"):
        record_type()


# ===========================================================================
# 件④ f107 「持续红」= 守卫时间窗误归因; 归因判定 attribute_rerun
# ===========================================================================

def test_item4_attribute_rerun_replays_f122_merge_window():
    """F122 合并后异步回归日志原形: f107 首轮期间 FOUNDATION-BACKLOG.md 变(loop 结案写编年史), 复核重跑期间 claimed→completed
    移卡 + closures/ 结案记录(同一结案步继续写)= 落点不重合 → 他方写入, 不归 f107。"""
    from tools.aipos_cli.runall_discovery import attribute_rerun

    g = "/h/lybra"
    first = [f"status {g}/governance/FOUNDATION-BACKLOG.md"]
    rerun = [f"status {g}/5_tasks/queue/claimed/aipos-f122.md", f"status {g}/5_tasks/queue/completed/aipos-f122.md",
             f"status {g}/5_tasks/records/closures/AIPOS-F122/close_AIPOS-F122_20261007_085029_exec.md"]
    assert attribute_rerun(first, rerun) == ([], rerun)
    # 真污染(同一文件再写 / 同目录新时间戳文件)= 复现
    assert attribute_rerun(first, [f"status {g}/governance/FOUNDATION-BACKLOG.md"]) == ([f"status {g}/governance/FOUNDATION-BACKLOG.md"], [])
    first = [f"status {g}/5_tasks/records/claims/X/claim_1.md"]
    assert attribute_rerun(first, [f"status {g}/5_tasks/records/claims/X/claim_2.md"])[0] == [f"status {g}/5_tasks/records/claims/X/claim_2.md"]
    assert attribute_rerun([f"md5 {g}/governance/a_log.md"], [f"status {g}/governance/a_log.md"])[0] == [], "检查类不同不算同一落点"


def test_item4_gate_close_during_rerun_is_concurrent_not_red(tmp_path):
    """执行器端到端: 测试首轮期间他方改治理文档, 复核重跑期间他方移卡 + 写结案记录(模拟 loop 结案与回归并发)→ 本文件 PASS。"""
    home, root = _fake_real_home(tmp_path)
    sentinel = tmp_path / "round"
    alpha = root / "alpha"
    repo = _product_repo(tmp_path, {
        "test_innocent.py": (
            "from pathlib import Path\n\n"
            "def test_innocent():\n"
            f"    sentinel, alpha = Path({str(sentinel)!r}), Path({str(alpha)!r})\n"
            "    if not sentinel.exists():  # 首轮时间窗: 他方写编年史\n"
            "        sentinel.write_text('1')\n"
            "        (alpha / 'governance' / 'FOUNDATION-BACKLOG.md').write_text('- A-1 closed\\n', encoding='utf-8')\n"
            "    elif sentinel.read_text() == '1':  # 复核重跑时间窗: 他方移卡 + 写结案记录\n"
            "        sentinel.write_text('2')\n"
            "        card = alpha / '5_tasks' / 'queue' / 'pending' / 'card-1.md'\n"
            "        (alpha / '5_tasks' / 'queue' / 'completed').mkdir(parents=True, exist_ok=True)\n"
            "        card.rename(alpha / '5_tasks' / 'queue' / 'completed' / 'card-1.md')\n"
            "        rec = alpha / '5_tasks' / 'records' / 'closures' / 'A-1'\n"
            "        rec.mkdir(parents=True, exist_ok=True)\n"
            "        (rec / 'close_A-1.md').write_text('---\\nrecord_type: closure\\n---\\n', encoding='utf-8')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[件④·复核窗口他方结案写入(执行器输出摘录)]\n" + "\n".join(
        ln for ln in text.splitlines() if "守卫" in ln or ln.startswith(("✓", "✗"))))
    assert rc == 0, text
    assert _verdict(text, "tests/test_innocent.py") == "PASS"
    assert "单独重跑未在同一落点复现 → 判为同时段他方写入" in text and "FOUNDATION-BACKLOG.md" in text
    assert "单独重跑期间他处变动(落点与本文件首轮变动不重合)" in text and "close_A-1.md" in text
    assert "测试所致变动 0 处" in text


def test_item4_timestamped_polluter_still_red(tmp_path):
    """真污染: 每次跑都往真实治理根同一目录写一条新时间戳记录 → 复核重跑在同一落点复现 → 红(修正不放过污染)。"""
    home, root = _fake_real_home(tmp_path)
    target = root / "beta" / "5_tasks" / "queue" / "pending"
    repo = _product_repo(tmp_path, {
        "test_polluter.py": (
            "import time\nfrom pathlib import Path\n\n"
            "def test_writes_real_root():\n"
            f"    Path({str(target)!r}, f'claim_{{time.time_ns()}}.md').write_text('x', encoding='utf-8')\n"
        ),
    })
    rc, text = _run_executor(repo, home)
    _show("[件④·时间戳污染(执行器输出摘录)]\n" + "\n".join(ln for ln in text.splitlines() if "守卫" in ln or ln.startswith(("✓", "✗"))))
    assert rc == 1 and _verdict(text, "tests/test_polluter.py") == "FAIL"
    assert "单独重跑本文件复现变动(判为本文件所致)" in text and "测试所致变动 1 处" in text


def test_item4_runner_uses_single_attribution():
    fn = _fn("tools/aipos_cli/runall_discovery.py", "run")
    assert "attribute_rerun" in _calls(fn)
