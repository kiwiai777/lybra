"""AIPOS-F252 — 受控发布面在没有 card_policy.json 的项目上不可用 + 阻塞交回被审计 PASS 后当成完成结案。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi / 任何真实生产主机): 复用 F251 夹具骨架(临时治理根 webdemo, 卡号形 WEB-<n>; 模拟主机 = 本机临时目录 +
PATH 最前的假 ssh), 但治理根**不放** card_policy.json(人肉期接入项目样本: kiwiai-web / chris / agency 同形, 在临时根自造)。
验收原文经 _show 打到 stdout(RETURN 证据)。
  件① 卡号形状声明: release plan 缺声明拒且出口带声明命令原文; 声明命令正反例(非法正则 / 不匹配现有卡号拒, 正确值写入并落账); 之后 F251 端到端
       (plan → apply → verify)通过; project upgrade 预演列出缺项; onboarding guide 给出命令。
  件② 阻塞交回: Return outcome=blocked → loop 推导不结案、派生 block 步、卡进 blocked + 收件箱事项 + 续跑出口; 审计 PASS 不产生 close;
       门侧 close 兜底拒; 旧交回(无 outcome)行为不变; outcome 值不合 = 入口拒。
  件③ 已误结案卡接续: completed 卡经 queue reopen 回到可续跑(新一轮只看本轮记录、旧报告归档), 再跑通发布链; completed 卡顾问异议 note 进收件箱。
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f251_release_surface as f251  # noqa: E402  — 发布面夹具(假 ssh / 模拟主机 / 声明 / 授权修订)
from test_aipos_f251_release_surface import rig as f251_rig  # noqa: E402,F401  — 夹具复用(pytest 按名注入)

_show = f251._show
_cli = f251._cli
PATTERN = "WEB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"


@pytest.fixture()
def rig(f251_rig):
    """F251 靶场去掉 card_policy.json(= 人肉期接入项目: 无卡号形状声明)。"""
    r = f251_rig
    (r["gov"] / "card_policy.json").unlink()
    f78._git(r["gov"], "add", "-A")
    f78._git(r["gov"], "commit", "-qm", "fixture: 人肉期样本(无 card_policy.json)")
    return r


def _cp(r: dict, *argv: str) -> tuple[int, str, str]:
    return _cli("project", "card-policy", *argv, "--workspace-root", r["gov"])


# ===========================================================================
# 件① 卡号形状声明可补
# ===========================================================================

def test_item1_release_plan_without_declaration_names_exit_command(rig):
    r = rig
    assert f251._declare_remote(r)[0] == 0
    rc, out, err = f251._plan(r)
    _show(f"---- 件① 无 card_policy.json 时 release plan exit {rc} ----\n{out}{err}")
    assert rc == 1 and "项目未声明 task_id_pattern" in err and "C2 原则" in err
    assert f"lybra --workspace-root {r['gov']} project card-policy set-task-id-pattern --pattern" in err
    assert "WEB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*" in err and "仅建议" in err  # 建议值来自队列现有卡号前缀, 须显式确认
    assert not (r["gov"] / "card_policy.json").exists()  # 无内置默认: 报错不写任何声明


def test_item1_declare_command_refusals_then_write_and_landing(rig):
    r = rig
    gov = r["gov"]
    rc, out, _err = _cp(r, "show")
    _show(f"---- 件① card-policy show(未声明)exit {rc} ----\n{out}")
    assert rc == 0 and "未声明 task_id_pattern" in out and PATTERN in out and "set-task-id-pattern" in out
    bad = {
        "非法正则": ("WEB-[0-9", "TASK_ID_PATTERN_INVALID"),
        "含捕获组": ("(WEB)-[0-9]+", "TASK_ID_PATTERN_INVALID"),
        "含锚点": ("^WEB-[0-9]+$", "TASK_ID_PATTERN_INVALID"),
        "匹配空串": ("(?:WEB-[0-9]+)?", "TASK_ID_PATTERN_INVALID"),
        "不匹配现有卡号(审计轮 WEB-3R)": ("WEB-[0-9]+", "TASK_ID_PATTERN_MISMATCH"),
        "空值": (" ", "TASK_ID_PATTERN_EMPTY"),
    }
    for what, (value, code) in bad.items():
        rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", value, "--confirm", "--actor", f251.ADVISOR)
        _show(f"[件① 拒·{what}] exit {rc}: {err.strip()}")
        assert rc == 1 and code in err, (what, err)
        assert not (gov / "card_policy.json").exists()
    assert "WEB-3R" in _cp(r, "set-task-id-pattern", "--pattern", "WEB-[0-9]+")[2]  # 拒因列出不匹配卡号
    rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR)
    _show(f"---- 件① set-task-id-pattern 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and "+  \"task_id_pattern\"" in out and not (gov / "card_policy.json").exists()
    rc, out, err = _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")
    _show(f"---- 件① set-task-id-pattern --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS" in out
    data = json.loads((gov / "card_policy.json").read_text(encoding="utf-8"))
    assert data["task_id_pattern"] == PATTERN and data["rules"] == []
    log = f78._git(gov, "log", "-1", "--format=%s")
    assert "卡号形状声明落账 set-task-id-pattern" in log, log
    assert f78._git(gov, "status", "--porcelain", "--", "card_policy.json") == ""
    from tools.card_policy_loader import get_task_id_pattern

    assert get_task_id_pattern(gov) == PATTERN  # 现行唯一读口认得
    # 现有声明文件: 只改 task_id_pattern 一键, 其余键原样
    data["rules"] = [{"field": "anchor_refs", "required": False, "values": ["N1"]}]
    (gov / "card_policy.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rc, out, _err = _cp(r, "set-task-id-pattern", "--pattern", "WEB-[A-Z0-9]+", "--actor", f251.ADVISOR, "--confirm")
    after = json.loads((gov / "card_policy.json").read_text(encoding="utf-8"))
    _show(f"[件① 改现有声明] exit {rc}; rules 保留={after['rules'] == data['rules']}; 值={after['task_id_pattern']}")
    assert rc == 0 and after["rules"] == data["rules"] and after["task_id_pattern"] == "WEB-[A-Z0-9]+"


def test_item1_after_declaration_f251_end_to_end_passes(rig):
    r = rig
    assert _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")[0] == 0
    assert f251._declare_remote(r)[0] == 0
    f251._auth_revision(r, action="grant", hosts=["devbox", f251.HOST], zones=["production"])
    before = f251._tree_state(r["prod"])
    rc, out, err = f251._plan(r, "--tree", r["tree"])
    _show(f"---- 件① 声明后 release plan exit {rc} ----\n{out}{err}")
    assert rc == 0 and "授权: 就绪" in out
    digest = f251._plan_hash(out)
    rc, out, err = f251._rel(r, "apply", "--task-id", f251.CARD, "--target", f251.TARGET, "--plan-hash", digest, "--actor", f251.EXEC)
    _show(f"---- 件① release apply exit {rc} ----\n{out}{err}")
    assert rc == 0 and "APPLY_OK" in out
    after = f251._tree_state(r["prod"])
    assert {k: v for k, v in after.items() if k in before} == before
    rc, out, err = f251._rel(r, "verify", "--task-id", f251.CARD, "--target", f251.TARGET)
    _show(f"---- 件① release verify exit {rc} ----\n{out}{err}")
    assert rc == 0 and "VERIFY_OK" in out


def test_item1_upgrade_preview_and_onboarding_list_the_step(rig, tmp_path):
    from tools.aipos_cli.onboarding import generate_onboarding_guide, legacy_onboarding_steps
    from tools.aipos_cli.project_upgrade import derive_guidance

    r = rig
    guidance = derive_guidance(r["gov"], project=f251.PROJECT)
    items = [i for g in guidance["groups"] for i in g["items"] if i["key"] == "task_id_pattern"]
    _show("---- 件① project upgrade 预演待补项 ----\n" + "\n".join(f"{i['why']} | {i['say']} | {i['commands']}" for i in items))
    assert items and any("project card-policy set-task-id-pattern" in c for c in items[0]["commands"])
    rc, out, err = _cli("project", "upgrade", "--workspace-root", r["gov"])
    _show(f"---- 件① lybra project upgrade(预演)exit {rc} ----\n" + "\n".join(l for l in (out + err).splitlines() if "task_id_pattern" in l or "card-policy" in l))
    assert "card-policy set-task-id-pattern" in out + err
    assert _cp(r, "set-task-id-pattern", "--pattern", PATTERN, "--actor", f251.ADVISOR, "--confirm")[0] == 0
    assert not [i for g in derive_guidance(r["gov"], project=f251.PROJECT)["groups"] for i in g["items"] if i["key"] == "task_id_pattern"]
    guide = generate_onboarding_guide("probe-cp", home_root=str(tmp_path / "h"), actor=f251.ADVISOR, repos=[f"app={tmp_path}/app"])
    step2 = next(s for s in guide["steps"] if s["step_number"] == 2)["command"]
    lines = [ln for ln in step2.splitlines() if "card-policy" in ln]
    _show("---- 件① onboarding guide 第 2 步卡号形状行 ----\n" + "\n".join(lines))
    assert any(ln.startswith("lybra --workspace-root") and "set-task-id-pattern" in ln for ln in lines)
    from tools.aipos_cli.aipos_cli import build_parser

    for ln in lines:
        if ln.startswith("lybra "):
            args = build_parser().parse_args(shlex.split(ln)[1:])
            assert args.command == "project" and args.cp_command in ("show", "set-task-id-pattern")
    legacy = legacy_onboarding_steps(f251.PROJECT, str(r["gov"]), home_root=str(tmp_path / "h"), actor=f251.ADVISOR, driver_policy="pol")
    assert "card-policy set-task-id-pattern" in legacy[0]["command"]
