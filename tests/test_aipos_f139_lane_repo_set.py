"""AIPOS-F139 — lane 选择允许仓集合(Owner 10-09 定方案 A): 信封 task_selector_lane_repo 与 --lane 视图接受多仓。

靶场 = 临时治理根(自造人肉期样本: 项目 hbjfx, 4 仓两子项目共用一仓 —— 子项目 OTA = ota-app + ota-contracts + 共用 shared-web;
子项目岚图 = lantu-app + 共用 shared-web)。禁真门/真治理根/真工位/真 pi; 夹具不起任何进程。
复用 F78C 靶场骨架(_gov_skeleton / _product_repo)、F133 卡片构造与 CLI 驱动(_hcard / _cli), 禁第二份靶场。

各件唯一并入点(禁第二路径):
  集合序列化/解析 = workspace_config.repo_name_set(铸造 owner_decision_writer._normalize_autonomy_policy / 渲染
      autonomy_policy.build_autonomy_policy_markdown / 读 normalize_policy / 判定 match_claim_envelope / 视图 resolve_lane_filter
      与 filter_rows_by_lane 共用)。
  件① 信封判定只在 autonomy_policy.match_claim_envelope(loop find_envelope → select_envelope 与门 _match_envelope_for_identities
      同一判据); 仓名解析只走 workspace_config.resolve_card_repo; CLI `lybra envelope mint --lane-repo` 可重复。
  件② 视图过滤只在 machine_zone.filter_rows_by_lane(四命令 next / brief / loop status / needs-owner 同一 --lane, 可重复)。
  件③ 文档 = docs/mcp-agent-setup.md、tools/aipos_cli/README.md、verbs.schema lane_view(help / repo_set)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import _fm, _write  # noqa: E402  — 靶场/替身唯一来源
from test_aipos_f78_engine_agnostic import DRIVER  # noqa: E402
from test_aipos_f78c_card_repo import _gov_skeleton, _product_repo  # noqa: E402
from test_aipos_f133_lane_view_dependencies import PROJECT, _cli, _hcard, _show  # noqa: E402
from tools.aipos_cli import machine_zone as mz  # noqa: E402

ADV_OTA = "advisor-ota.hbjfx.h1"
ADV_LT = "advisor-lantu.hbjfx.h1"
ADV_OLD = "advisor-old.hbjfx.h1"
POL_OTA, POL_LT, POL_OLD = "pol_f139_ota", "pol_f139_lantu", "pol_f139_old"
OTA_SET = ["ota-app", "ota-contracts", "shared-web"]
LT_SET = ["lantu-app", "shared-web"]
CARDS = {"HBJ-OA1": "ota-app", "HBJ-OC1": "ota-contracts", "HBJ-SW1": "shared-web", "HBJ-LT1": "lantu-app"}
OLD_ENVELOPE_LANE = "ota-app"


def _policy(gov: Path, pid: str, agent: str, lanes: list[str]) -> Path:
    """新形信封: 唯一渲染 build_autonomy_policy_markdown(仓集合经 repo_name_set 落列表)。"""
    from tools.aipos_cli.autonomy_policy import build_autonomy_policy_markdown

    path = gov / "5_tasks" / "policies" / f"{pid}.md"
    _write(path, build_autonomy_policy_markdown(
        policy_id=pid, agent_or_role=agent, active_from="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
        max_tasks=20, owner_approval_ref="fixture-envelope", task_selector_task_mode="code", task_selector_lane_repo=lanes))
    return path


def _legacy_single_value_policy(gov: Path) -> Path:
    """存量单值信封(F134 期铸造形: task_selector_lane_repo 为标量字符串)——按存量文本逐字写入, 不经新渲染。"""
    path = gov / "5_tasks" / "policies" / f"{POL_OLD}.md"
    _write(path, _fm({
        "record_type": "owner_autonomy_policy", "policy_id": POL_OLD, "mode": "PreAuthorized", "status": "active",
        "approved_by_owner": True, "owner_approval_ref": "fixture-envelope-old", "active_from": "2026-01-01T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z", "agent_or_role": ADV_OLD, "task_selector_task_mode": "code",
        "task_selector_project": "", "task_selector_task_ids": [], "max_tasks": 20, "launch_harnesses": [],
        "task_selector_lane_repo": OLD_ENVELOPE_LANE,
    }, "# Owner Autonomy Policy: legacy single-value lane\n"))
    return path


@pytest.fixture
def quad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gov = _gov_skeleton(tmp_path / "gov-hbjfx", monkeypatch)
    for sub in ("stage_archive", "governance/decision_log"):  # lybra brief 的最小治理档骨架(F133 夹具同形)
        (gov / sub).mkdir(parents=True)
    repos = {name: _product_repo(tmp_path / f"repo-{name}") for name in ("ota-app", "ota-contracts", "shared-web", "lantu-app")}
    _write(gov / "project.json", json.dumps({"project": PROJECT, "config_version": 1,
                                             "repos": {"default": "ota-app", "items": {k: str(v) for k, v in repos.items()}}}))
    for tid, repo in CARDS.items():
        _hcard(gov, tid, "pending", lane_repo=repo, priority="medium", needs_owner=True)
    _hcard(gov, "HBJ-X1", "pending", lane_repo="ghost", priority="low", needs_owner=True)  # 未声明仓 → lane 不可解析(不带 --lane 照列; 带 --lane 不混入, 单独计数 AIPOS-F141)
    return gov, repos


def _card_fm(gov: Path, tid: str) -> dict:
    from tools.aipos_cli.frontmatter import require_frontmatter
    from tools.aipos_cli.task_loader import find_task_card

    fm, _ = require_frontmatter(find_task_card(gov, tid)[0])
    return fm


def _find(gov: Path, tid: str, actor: str):
    from tools.aipos_cli.loop_driver import find_envelope

    return find_envelope(gov, task_id=tid, task_fm=_card_fm(gov, tid), driver_actor=actor, driver_role="advisor")


# ===========================================================================
# 集合序列化/解析唯一函数
# ===========================================================================

def test_repo_name_set_single_function():
    from tools.aipos_cli.workspace_config import repo_name_set

    assert repo_name_set(None) == [] == repo_name_set("") == repo_name_set([]) == repo_name_set(["", "  "])
    assert repo_name_set("ota-app") == ["ota-app"]  # 存量单值 = 单元素集合
    assert repo_name_set([" a ", "b", "a"]) == ["a", "b"]  # 去空去重保序
    assert repo_name_set("a,b") == ["a,b"]  # 写法 = 可重复参数, 不按逗号拆(仓指称可为路径)
    for bad in ({"a": 1}, 3, ["a", 1]):
        with pytest.raises(ValueError):
            repo_name_set(bad)
    # 铸造 / 渲染 / 读 / 判定 / 视图全部经此一个函数(禁第二路径)
    src = {p: (REPO_ROOT / "tools/aipos_cli" / p).read_text(encoding="utf-8")
           for p in ("autonomy_policy.py", "owner_decision_writer.py", "machine_zone.py", "aipos_cli.py")}
    assert src["autonomy_policy.py"].count("repo_name_set(") >= 4  # 渲染 / normalize_policy / match_claim_envelope / select_envelope
    assert "repo_name_set(" in src["owner_decision_writer.py"] and "repo_name_set(" in src["aipos_cli.py"]
    assert src["machine_zone.py"].count("repo_name_set(") >= 3  # resolve_lane_filter / lane_filter_label / filter_rows_by_lane
    assert sum((REPO_ROOT / "tools/aipos_cli" / p).read_text(encoding="utf-8").count("def repo_name_set(")
               for p in ("workspace_config.py", "autonomy_policy.py", "machine_zone.py", "owner_decision_writer.py", "aipos_cli.py")) == 1


# ===========================================================================
# 件① 信封仓集合: 推本子项目各仓通过 / 推他子项目独有仓拒(原因链列集合) / 共用仓两信封均通过 / 单值存量不变 / 铸造不可解析拒
# ===========================================================================

def test_item1_set_envelope_covers_own_subproject_and_shared_repo(quad):
    gov, _ = quad
    _policy(gov, POL_OTA, ADV_OTA, OTA_SET)
    _policy(gov, POL_LT, ADV_LT, LT_SET)
    text = (gov / "5_tasks" / "policies" / f"{POL_OTA}.md").read_text(encoding="utf-8")
    _show("[件①·OTA 信封(仓集合)frontmatter]\n" + text.split("\n---\n", 1)[0])
    assert "task_selector_lane_repo:\n- ota-app\n- ota-contracts\n- shared-web\n" in text
    assert "resolves to one of `ota-app`, `ota-contracts`, `shared-web` only" in text

    outcome = {}
    for actor, pid in ((ADV_OTA, POL_OTA), (ADV_LT, POL_LT)):
        for tid in CARDS:
            pol, reasons = _find(gov, tid, actor)
            outcome[(actor, tid)] = (pol["policy_id"] if pol else None, reasons)
            _show(f"[件①·loop find_envelope] actor={actor} card={tid}(lane.repo={CARDS[tid]}) → "
                  f"{pol['policy_id'] if pol else 'REFUSED'} {reasons if not pol else ''}")
    # OTA 信封: OTA 各仓卡 + 共用仓卡通过; 岚图独有仓卡拒, 原因链列出整个集合
    for tid in ("HBJ-OA1", "HBJ-OC1", "HBJ-SW1"):
        assert outcome[(ADV_OTA, tid)][0] == POL_OTA
    pid, reasons = outcome[(ADV_OTA, "HBJ-LT1")]
    assert pid is None and any(r.startswith(f"{POL_OTA}: card lane.repo resolves to")
                               and f"task_selector.lane_repo={OTA_SET!r}" in r for r in reasons), reasons
    # 岚图信封: 岚图仓卡 + 共用仓卡通过; OTA 独有仓卡拒(原因链列集合)
    for tid in ("HBJ-LT1", "HBJ-SW1"):
        assert outcome[(ADV_LT, tid)][0] == POL_LT
    for tid in ("HBJ-OA1", "HBJ-OC1"):
        pid, reasons = outcome[(ADV_LT, tid)]
        assert pid is None and any(f"task_selector.lane_repo={LT_SET!r}" in r for r in reasons), reasons


def test_item1_gate_side_same_judge_and_error_code(quad):
    """门侧逐请求核验(_match_envelope_for_identities → envelope_subject + match_claim_envelope)与 loop 同一判据。"""
    from tools.aipos_cli.autonomy_policy import driver_envelope_identities, load_policy
    from tools.mcp_server import tools as gate

    gov, _ = quad
    _policy(gov, POL_OTA, ADV_OTA, OTA_SET)
    policy = load_policy(gov, POL_OTA)
    assert policy["task_selector_lane_repo"] == OTA_SET
    ids = driver_envelope_identities(ADV_OTA, "advisor", "advisor")
    res = {tid: gate._match_envelope_for_identities(gov, policy=policy, snapshot=_card_fm(gov, tid), task_id=tid,
                                                    identities=ids, released_count=0) for tid in CARDS}
    _show("[件①·门侧] " + json.dumps({k: [v[0], v[2]] for k, v in res.items()}, ensure_ascii=False))
    assert all(res[t][0] for t in ("HBJ-OA1", "HBJ-OC1", "HBJ-SW1"))
    assert res["HBJ-LT1"][0] is False and res["HBJ-LT1"][2] == "ENVELOPE_SELECTOR_LANE_REPO_MISMATCH"
    assert f"task_selector.lane_repo={OTA_SET!r}" in res["HBJ-LT1"][1]


def test_item1_legacy_single_value_envelope_unchanged(quad):
    gov, _ = quad
    path = _legacy_single_value_policy(gov)
    before = path.read_bytes()
    _show("[件①·存量单值信封 frontmatter 行] "
          + next(line for line in before.decode("utf-8").splitlines() if line.startswith("task_selector_lane_repo")))
    from tools.aipos_cli.autonomy_policy import load_policy

    assert load_policy(gov, POL_OLD)["task_selector_lane_repo"] == [OLD_ENVELOPE_LANE]  # 按单元素集合读
    ok, _ = _find(gov, "HBJ-OA1", ADV_OLD)
    for tid in ("HBJ-OC1", "HBJ-SW1", "HBJ-LT1"):
        none, reasons = _find(gov, tid, ADV_OLD)
        _show(f"[件①·存量单值信封推 {tid}] REFUSED {reasons}")
        assert none is None and any(f"task_selector.lane_repo={[OLD_ENVELOPE_LANE]!r}" in r for r in reasons)
    assert ok and ok["policy_id"] == POL_OLD
    assert path.read_bytes() == before  # 存量逐字节不变(读与判定不改写文件)


def test_item1_mint_repeatable_set_and_unresolvable_refused(quad):
    from tools.aipos_cli.aipos_cli import _envelope_mint_payload, build_parser
    from tools.aipos_cli.autonomy_policy import load_policy
    from tools.aipos_cli.owner_decision_writer import build_owner_decision_record

    gov, repos = quad
    args = build_parser().parse_args(["envelope", "mint", "--policy-id", "pol_f139_m", "--agent-or-role", ADV_OTA, "--max-tasks", "5",
                                      "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z", "--decision-summary", "s",
                                      "--lane-repo", "ota-app", "--lane-repo", "ota-contracts", "--lane-repo", str(repos["shared-web"]),
                                      "--dry-run"])
    assert args.lane_repo == ["ota-app", "ota-contracts", str(repos["shared-web"])]
    payload = _envelope_mint_payload(policy_id="pol_f139_m", agent_or_role=ADV_OTA, max_tasks=5, task_mode="code",
                                     expires_at="2999-01-01T00:00:00Z", decision_summary="s", actor="owner", lane_repo=args.lane_repo)
    res = build_owner_decision_record(gov, payload, actor="owner", dry_run=False)
    assert not res.get("blocking_reasons"), res
    text = (gov / "5_tasks" / "policies" / "pol_f139_m.md").read_text(encoding="utf-8")
    _show("[件①·mint --lane-repo ×3 落盘信封 frontmatter]\n" + text.split("\n---\n", 1)[0])
    shared = str(repos["shared-web"].resolve())
    assert f"task_selector_lane_repo:\n- ota-app\n- ota-contracts\n- {shared}\n" in text  # 路径形落 resolve 后绝对路径
    assert load_policy(gov, "pol_f139_m")["task_selector_lane_repo"] == ["ota-app", "ota-contracts", shared]
    pol, _ = _find(gov, "HBJ-SW1", ADV_OTA)  # 路径形成员经 card_lane_refs(解析路径)命中共用仓卡
    assert pol and pol["policy_id"] == "pol_f139_m"

    # 集合内含不可解析仓名 = 拒(逐个点名, 零写入); CLI --dry-run 原文
    rc, out, err = _cli(["envelope", "mint", "--workspace-root", str(gov), "--policy-id", "pol_f139_bad", "--agent-or-role", ADV_OTA,
                         "--max-tasks", "5", "--task-mode", "code", "--expires-at", "2999-01-01T00:00:00Z",
                         "--decision-summary", "s", "--lane-repo", "ota-app", "--lane-repo", "nope-repo", "--dry-run"])
    _show(f"[件①·mint --lane-repo ota-app --lane-repo nope-repo --dry-run] rc={rc}\n{out}{err}")
    report = json.loads(out)
    assert rc == 1 and report["verdict"] == "BLOCK"
    assert "['lantu-app', 'ota-app', 'ota-contracts', 'shared-web']" in json.dumps(report, ensure_ascii=False)  # 判于靶场根仓清单
    assert any("lane_repo='nope-repo' 不可解析" in r and "LANE_REPO_UNDECLARED" in r and "['ota-app', 'nope-repo']" in r
               for r in report["blocking_reasons"]), report["blocking_reasons"]
    assert not any("'ota-app' 不可解析" in r for r in report["blocking_reasons"])
    assert not (gov / "5_tasks" / "policies" / "pol_f139_bad.md").exists()


# ===========================================================================
# 件② 四命令 --lane 可重复(仓集合)同一过滤
# ===========================================================================

def test_item2_four_commands_multi_repo_lane_filter(quad):
    from tools.aipos_cli.loop_driver import load_loop_contract
    from tools.aipos_cli.loop_run_record import LoopRunRecorder

    gov, repos = quad
    unresolved = mz.lane_view_declaration()["unresolved_lane"]
    lane_args = ["--lane", "ota-contracts", "--lane", "shared-web"]
    assert mz.resolve_lane_filter(gov, ["ota-contracts", str(repos["shared-web"])]) == ["ota-contracts", "shared-web"]

    rc, out, err = _cli(["next", "--workspace-root", str(gov), *lane_args])
    _show("[件②·lybra next --lane ota-contracts --lane shared-web]\n" + out)
    assert rc == 0, err
    assert "lane ota-contracts, shared-web" in out.splitlines()[1]  # AIPOS-F144: 首行 = 治理根标注
    listed = out.split("未归 lane", 1)[0]  # AIPOS-F141 件②: 不可解析 lane 不混入, 末尾单独计数
    assert "HBJ-OC1" in listed and "HBJ-SW1" in listed and "HBJ-X1" not in listed and "HBJ-X1" in out.split("未归 lane", 1)[1]
    assert "HBJ-OA1" not in out and "HBJ-LT1" not in out

    rc, out, err = _cli(["brief", "--workspace-root", str(gov), *lane_args, "--json"])
    queue = json.loads(out)["queue"]
    _show("[件②·lybra brief --lane ×2 --json queue] " + json.dumps(
        {"lane_filter": queue["lane_filter"], "pending": queue["pending"], "lanes": list(queue["lanes"])}, ensure_ascii=False))
    assert rc == 0, err
    assert queue["lane_filter"] == ["ota-contracts", "shared-web"] and queue["pending"] == 2  # AIPOS-F141: 不可解析不混入
    assert list(queue["lanes"]) == ["ota-contracts", "shared-web"]  # 声明序; 未解析 lane 单独成「未归 lane」组
    assert [u["task_id"] for u in queue["unresolved_lane"]] == ["HBJ-X1"]

    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", *lane_args])
    _show("[件②·lybra needs-owner --lane ×2]\n" + out)
    assert rc == 0, err
    # AIPOS-F144 件②: 首行 = 解析到的治理根项目与来源; 视图标题顺延到第二行
    assert out.splitlines()[0].startswith("治理根: 项目 ") and str(gov.resolve()) in out.splitlines()[0]
    assert out.splitlines()[1] == "Needs Owner — lane ota-contracts, shared-web"
    listed = out.split("未归 lane", 1)[0]
    assert "HBJ-OC1" in listed and "HBJ-SW1" in listed and "HBJ-X1" not in listed and "HBJ-OA1" not in out and "HBJ-LT1" not in out
    assert "HBJ-X1" in out.split("未归 lane", 1)[1]
    rc, out, err = _cli(["--workspace-root", str(gov), "needs-owner", "--json"])
    lanes = json.loads(out)["lanes"]
    assert list(lanes) == ["ota-app", "ota-contracts", "shared-web", "lantu-app", unresolved]  # 不带 --lane 分组不变

    recorders = [LoopRunRecorder(gov, tid, driver=DRIVER, contract=load_loop_contract(), warn=lambda _m: None) for tid in CARDS]
    try:
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), *lane_args])
        _show("[件②·lybra loop status --lane ×2]\n" + out)
        assert rc == 0, err
        assert "卡 HBJ-OC1  lane ota-contracts" in out and "卡 HBJ-SW1  lane shared-web" in out
        assert "HBJ-OA1" not in out and "HBJ-LT1" not in out
        rc, out, err = _cli(["loop", "status", "--workspace-root", str(gov), *lane_args, "--json"])
        assert json.loads(out)["lane_filter"] == ["ota-contracts", "shared-web"]
    finally:
        for rec in recorders:
            rec.close_log()

    # 集合内任一未声明 = 拒(点名该值与可选值), 四命令同一出口码
    for argv in (["next", "--workspace-root", str(gov)], ["brief", "--workspace-root", str(gov)],
                 ["--workspace-root", str(gov), "needs-owner"], ["loop", "status", "--workspace-root", str(gov)]):
        rc, _, err = _cli([*argv, "--lane", "ota-app", "--lane", "ghost"])
        assert rc == mz.lane_view_declaration()["invalid_lane_exit_code"] and "'ghost'" in err and "可选" in err, (argv, rc, err)
    _show(f"[件②·--lane ota-app --lane ghost] rc={rc} {err.strip()}")


def test_item2_single_filter_function_and_declaration():
    from tools.schema_loader import load_schema

    decl = load_schema("verbs")["lane_view"]
    assert "可重复" in decl["help"] and "repo_name_set" in decl["repo_set"] and "F135" in decl["repo_set"]
    src = (REPO_ROOT / "tools/aipos_cli/machine_zone.py").read_text(encoding="utf-8")
    assert src.count("def filter_rows_by_lane(") == 1 and 'action="append"' in src.split("def add_lane_argument(", 1)[1].split("\ndef ", 1)[0]
    rows = [{"lane": "a"}, {"lane": "b"}, {"lane": "c"}, {"lane": decl["unresolved_lane"]}]
    # AIPOS-F141 件②: 未解析 lane 不混入所选 lane(set_aside 收集供「未归 lane」组计数)
    aside: list = []
    assert mz.filter_rows_by_lane(rows, ["a", "c"], set_aside=aside) == [rows[0], rows[2]] and aside == [rows[3]]
    assert mz.filter_rows_by_lane(rows, "b") == [rows[1]]  # 单值调用方(结案取下一张按本卡 lane)
    assert mz.filter_rows_by_lane(rows, None) == rows


# ===========================================================================
# 件③ 文档
# ===========================================================================

def test_item3_docs_subproject_cross_repo():
    setup = (REPO_ROOT / "docs/mcp-agent-setup.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "tools/aipos_cli/README.md").read_text(encoding="utf-8")
    assert "Sub-projects that span several repos" in setup and "--lane-repo <repo a> --lane-repo <repo b>" in setup
    assert "AIPOS-F135" in setup and "single repo keeps working unchanged" in setup
    assert "子项目跨仓" in readme and "共用仓可同时出现在多个顾问的集合里" in readme and "F135 自动排队" in readme
