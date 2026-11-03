"""AIPOS-F288 — 机制小遗留收口。

件① 例行拒连败按「同族码」计数: verbs.schema lybra_loop_status.next_action.routine_rejections.families(族声明, 至少 基线落后族 =
     BRANCH_WRONG_BASE + MERGE_CONFLICT; 人读名 family_texts 双语块), 连败计数唯一实现 loop_run_record.routine_streaks 按族计(无族 = 按码,
     行为不变); loop status 单列同族连败行、连败说明带族名; 运行记录 routine_relaunches[].families 记族名。
件② 手动队列变更自动落账: 手敲 `lybra queue reopen`(同族 block / withdraw …)写入成功后即经 F160 / F285 同一落账实现
     (board_adapter.land_after_write → governance_commit.land_written_paths)提交推送本次路径; 他人暂存原样保留、不混入; 落账失败撤回
     本次暂存并给重试命令; loop 续跑的 N6 收账不重复落账 reopen 的路径。
件③ 认领记录名同秒不撞名: record_writer.claim_runtime_ids(唯一生成口, 同秒加单调序号 _02 …), 读侧存量命名照读。
件④ 一键推荐写入 YAML 遇双语对象报 RepresenterError: 根因 = record_writer._FrontmatterDumper(SafeDumper 子类)在 i18n 导入前建成时
     复制的 representer 表里没有 Localized; 修在写入口一处(record_writer._dump_frontmatter_yaml 先经 _plain_scalars 转普通 str)。

靶场一律临时根(临时治理根 / 临时产品仓 / 临时 HOME; 假 pi = PATH 里的 tests/fake_pi.py 薄壳), 复用 F90 / F95 / F252 / F275 / F278 / F282
既有夹具; 夹具子进程只经 tools/aipos_cli/tests/fixture_procs.py 起停。不碰真实治理根 / 真实门 / 真实工位, 不读真实 connection.json。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f95_harness_launch as f95  # noqa: E402
import test_aipos_f252_card_policy_blocked_return as f252  # noqa: E402
import test_aipos_f275_routine_relaunch as f275  # noqa: E402
import test_aipos_f278_finalize_conflict_relaunch as f278  # noqa: E402
import test_aipos_f282_advisor_reopen_enroll_deliver_project as f282  # noqa: E402
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest fixture(lrig 依赖)
from test_aipos_f90_loop_one_stage import rig as f90_rig  # noqa: E402,F401  — pytest fixture
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — pytest fixture(拉起靶场 + 收尾无残留断言)
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import run_loop  # noqa: E402
from tools.aipos_cli.tests import fixture_procs  # noqa: E402
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

TASK = f90.TASK
BWB, MC = "BRANCH_WRONG_BASE", "MERGE_CONFLICT"


def _show(text: str) -> None:
    print(text, flush=True)


def _routine() -> dict:
    return lrr.status_contract()["next_action"]["routine_rejections"]


# ===========================================================================
# 件① 同族码: 声明 + 计数唯一实现 + fail-closed
# ===========================================================================

def test_item1_family_declared_and_streaks_count_by_family():
    routine = _routine()
    fam = lrr.routine_families(routine)
    _show(f"[件① 族声明] families={json.dumps({k: v for k, v in routine['families'].items() if k != 'description'}, ensure_ascii=False)}")
    assert fam[BWB] == fam[MC] == "base_behind"
    assert routine["family_texts"]["zh"]["names"]["base_behind"] and routine["family_texts"]["en"]["names"]["base_behind"]
    # 同族交替: MC → BWB → 本次 BWB = 3(合并计数); 并入码按声明序列出
    got = lrr.routine_streaks([BWB], [[MC], [BWB]], routine)
    _show(f"[件① 同族交替 MC→BWB→BWB] {json.dumps(got, ensure_ascii=False)}")
    assert got[BWB] == {"streak": 3, "family": "base_behind", "merged": [BWB, MC]}
    # 断链: 中间夹非本族码 / 未结束 = 断
    assert lrr.routine_streaks([BWB], [[MC], ["REPO_MERGE_IN_PROGRESS"], [BWB]], routine)[BWB]["streak"] == 2
    assert lrr.routine_streaks([MC], [[BWB], None], routine)[MC]["streak"] == 1
    # 不属任何族的码 = 按码计(行为不变)
    assert lrr.routine_streaks(["REPO_MERGE_IN_PROGRESS"], [[BWB], ["REPO_MERGE_IN_PROGRESS"]], routine)["REPO_MERGE_IN_PROGRESS"] == {
        "streak": 2, "family": None, "merged": ["REPO_MERGE_IN_PROGRESS"]}
    # 无 families 声明 = F288 前行为(全按码计)
    legacy = {k: v for k, v in routine.items() if k not in ("families", "family_texts")}
    assert lrr.routine_streaks([BWB], [[MC], [BWB]], legacy)[BWB]["streak"] == 2  # 只数尾段的 BWB, MC 断链
    # 族说明只在族内确有他码并入时出现(纯单码连败不带说明, 原文不变)
    assert lrr.routine_family_note(routine, got[BWB]) == "(同族「基线落后族」合并计数: BRANCH_WRONG_BASE / MERGE_CONFLICT)"
    assert lrr.routine_family_note(routine, lrr.routine_streaks([MC], [[MC]], routine)[MC]) == ""


def test_item1_family_note_follows_user_language(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    routine = _routine()
    detail = lrr.routine_streaks([BWB], [[MC]], routine)[BWB]
    monkeypatch.setenv("LYBRA_LANG", "en")
    en_note = lrr.routine_family_note(routine, detail)
    en_lines = lrr.routine_family_status_lines(routine, {BWB: detail}, 3)
    _show(f"[件① en] note={en_note!r} status={en_lines!r}")
    assert en_note == ' (same family "base-behind family" counted together: BRANCH_WRONG_BASE / MERGE_CONFLICT)'
    assert en_lines == ["Same-family streak: base-behind family (BRANCH_WRONG_BASE / MERGE_CONFLICT) counted together, now 2/3"]
    assert not f282.CJK.search(en_note + "".join(en_lines))


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r["families"]["base_behind"].__setitem__("codes", ["NOT_A_ROUTINE_CODE"]), "不在 routine_rejections.codes"),
    (lambda r: r["families"].__setitem__("other_family", {"codes": [MC]}), "同时属"),
    (lambda r: r["families"]["base_behind"].__setitem__("codes", []), "非空列表"),
    (lambda r: r["families"].__setitem__("Bad-Name", {"codes": ["REPO_MERGE_IN_PROGRESS"]}), "小写标识符"),
    (lambda r: r["family_texts"]["en"]["names"].pop("base_behind"), "family_texts"),
    (lambda r: r.pop("family_texts"), "family_texts"),
])
def test_item1_family_declaration_fail_closed(mutate, needle):
    routine = copy.deepcopy(_routine())
    mutate(routine)
    with pytest.raises(SchemaLoadError) as exc:
        lrr._check_routine_rejections(routine)
    _show(f"[件① fail-closed] {str(exc.value)[:200]}")
    assert needle in str(exc.value)


def test_acceptance1_pi_alternating_same_family_third_owner_needed(lrig, monkeypatch):
    """验收①: pi 执行体 finalize 步 MERGE_CONFLICT → 交回步 BRANCH_WRONG_BASE 交替不修, 第 3 次同族即不再重拉 → owner_needed。"""
    f275._launch_rig(lrig, monkeypatch, "merge_conflict_nofix")
    calls = f278._finalize_with_conflicts(monkeypatch, conflicts=1)
    f90._card(lrig.gov, f95.TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    res, text = f278._run(lrig)
    limit = _routine()["repeat_owner_needed"]
    _show("---- 验收① lybra loop 输出原文(节选: 账务步 / 例行拒自动重拉 / 结束) ----\n"
          + f278._lines(text, "] run ", "routine_relaunch:", "exit "))
    assert res.outcome == "gate_rejected", text
    assert [(c["blocked"], c["category"]) for c in calls] == [(True, MC)]
    assert f"例行拒自动重拉 第 1/{limit} 次: finalize 被门拒 拒因码 {MC}" in text
    assert f"例行拒自动重拉 第 2/{limit} 次: 交回被门拒 拒因码 {BWB}" in text
    assert (f"例行拒自动重拉不适用(本卡 {BWB} 连续第 {limit}/{limit} 次仍拒(同族「基线落后族」合并计数: {BWB} / {MC}), "
            "不再重拉(loop status 判 owner_needed)), 照旧以门拒结束") in text
    meta = f275._records(lrig.gov)[-1]
    rel = meta["routine_relaunches"]
    _show("[验收①] 运行记录 routine_relaunches(attempt / codes / step / families): "
          + json.dumps([(r["attempt"], r["codes"], r["step"], r["families"]) for r in rel], ensure_ascii=False))
    assert [(r["attempt"], r["codes"], r["step"], r["families"]) for r in rel] == [
        (1, [MC], "finalize", {MC: "base_behind"}), (2, [BWB], "return", {BWB: "base_behind"})]
    assert meta["end_codes"] == [BWB]
    rc, out = f278._status(lrig.gov, "--wait", "5")
    _show(f"---- 验收① lybra loop status --wait 5 原文 exit {rc} ----\n{out}")
    na = json.loads(f278._cli(["loop", "status", "--task-id", f95.TASK, "--workspace-root", str(lrig.gov), "--json"])[1])["next_action"]
    _show("[验收①] --json next_action(action / reason)原文: " + json.dumps(
        {"action": (na.get("tier_from") or na)["action"], "reason": na["reason"], "family_lines": na.get("family_lines")}, ensure_ascii=False))
    assert (na.get("tier_from") or na)["action"] == "owner_needed" and na["reason"] == "routine_fix_exhausted", na
    assert rc == declared_exit_code("lybra_loop_status", "investigate")
    assert f"本卡 {BWB} 连续 {limit} 次(同族「基线落后族」合并计数: {BWB} / {MC})" in out
    assert f"同族连败: 基线落后族({BWB} / {MC})合并计数, 当前第 {limit}/{limit} 次" in out


# ===========================================================================
# 件② 手敲 queue reopen 写后即自动落账(他人暂存不混入); 失败撤回本次暂存 + 重试命令; loop N6 不重复落账
# ===========================================================================

def _other_staged(gov: Path) -> str:
    """另一会话 / 另一项目留在共享治理仓暂存区的改动(11-02 / 11-03 现场形): 在本次 reopen 路径之外。"""
    f78._write(gov / "governance" / "OTHER-SESSION.md", "---\nstatus: draft\n---\n# 别人的暂存\n")
    f78._git(gov, "add", "--", "governance/OTHER-SESSION.md")
    return f78._git(gov, "diff", "--cached")


def test_acceptance2_hand_reopen_lands_own_commit_staging_clean_then_n6_no_duplicate(f90_rig):
    r = f90_rig
    f282._blocked_card(r)
    staged_before = _other_staged(r.gov)
    head_before = f78._git(r.gov, "rev-parse", "HEAD")
    rc, out, err = f282._reopen(r, f78.DRIVER)
    _show(f"---- 验收② 手敲 lybra queue reopen exit {rc} ----\n" + "\n".join(
        ln for ln in (out + err).splitlines() if "落账" in ln or "verdict" in ln.lower()))
    assert rc == 0, out + err
    log = f78._git(r.gov, "log", "--format=%h %s", f"{head_before}..HEAD")
    files = f78._git(r.gov, "show", "--name-only", "--format=", "HEAD")
    cached = f78._git(r.gov, "diff", "--cached", "--name-only")
    _show(f"[验收② git log {head_before[:8]}..HEAD]\n{log}\n[验收② 该提交文件]\n{files}\n[验收② 暂存区(git diff --cached --name-only)]\n{cached}")
    assert log.splitlines() and log.splitlines()[0].endswith(f"chore(governance): 队列写落账 reopen {TASK}") and len(log.splitlines()) == 1
    assert "governance/OTHER-SESSION.md" not in files  # 他人暂存不混入本次提交
    assert f"5_tasks/queue/pending/{TASK.lower()}.md" in files and f"5_tasks/records/events/{TASK}/" in files
    assert f78._git(r.gov, "diff", "--cached") == staged_before  # 暂存区只剩他人原样暂存, reopen 路径无残留
    assert f78._git(r.gov, "status", "--porcelain", "--", "5_tasks/queue", "5_tasks/records/events") == ""
    assert f78._git(r.gov, "ls-remote", "origin", "refs/heads/main").split()[0] == f78._git(r.gov, "rev-parse", "HEAD")  # 已推送
    # loop 续跑结案: N6 收账不再落 reopen 的路径(由 reopen 自身落账, 不重复)
    f78._git(r.gov, "reset", "-q", "--", "governance/OTHER-SESSION.md")
    (r.gov / "governance" / "OTHER-SESSION.md").unlink()
    out_io = io.StringIO()
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out_io, watch=f90._agent_watch(r, []), interval=0.05,
                   max_wait=5, max_steps=30)
    assert res.outcome == "completed", out_io.getvalue()
    reopen_paths = [ln for ln in f78._git(r.gov, "log", "--name-only", "--format=", f"{head_before}..HEAD").splitlines()
                    if f"events/{TASK}/reopen_round_" in ln]
    _show(f"[验收② loop 续跑后] 含 reopen 轮界记录的提交文件行: {reopen_paths}")
    assert len(reopen_paths) == 1  # 轮界记录只在 reopen 自身落账提交里出现一次


def test_acceptance2_hand_reopen_landing_failure_unstages_and_gives_retry(f90_rig):
    r = f90_rig
    f282._blocked_card(r)
    staged_before = _other_staged(r.gov)
    hook = r.gov / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'blocked by F288 fixture hook' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    rc, out, err = f282._reopen(r, f78.DRIVER, "--json")
    landing = json.loads(out)["landing"]
    _show(f"---- 验收② 落账失败 exit {rc} ----\nlanded={landing['landed']} index_restored={landing.get('index_restored')}\n"
          f"remedy={landing.get('remedy')}")
    assert rc == int(landing["exit_code"]) and rc != 0  # 以声明 landing_failed_exit_code 退出
    assert landing["landed"] is False and landing.get("index_restored") is True
    assert f78._git(r.gov, "diff", "--cached") == staged_before  # 本次路径已撤出暂存区, 他人暂存不动
    assert (r.gov / "5_tasks" / "queue" / "pending" / f"{TASK.lower()}.md").is_file()  # 写入已生效(不回滚)
    hook.unlink()
    import shlex

    rc2, out2, err2 = f252.f90_cli(shlex.split(landing["remedy"])[1:])
    _show(f"[验收② 照重试落账命令] rc={rc2}: {(out2 + err2).strip().splitlines()[-1] if (out2 + err2).strip() else ''}")
    assert rc2 == 0 and f78._git(r.gov, "status", "--porcelain", "--", "5_tasks/queue", "5_tasks/records/events") == ""
    assert f78._git(r.gov, "diff", "--cached") == staged_before


# ===========================================================================
# 件③ 认领记录名: 同秒重认领不撞名(唯一生成口 claim_runtime_ids); 读侧兼容存量命名
# ===========================================================================

def test_item3_claim_runtime_ids_monotonic_suffix(tmp_path):
    from tools.aipos_cli.record_writer import CLAIM_ID_SEQ_MAX, claim_record_paths, claim_runtime_ids

    gov = tmp_path / "gov"
    stamp, actor = "2026-11-03T07:17:29Z", "exec.lybra.test"
    first = claim_runtime_ids(gov, TASK, stamp, actor)
    assert first == (f"claim_{TASK}_20261103_071729_exec-lybra-test", f"session_{TASK}_20261103_071729_exec-lybra-test")
    seen = [first]
    for _ in range(3):
        for path in claim_record_paths(gov, TASK, *seen[-1]):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("---\nclaim_id: x\n---\n", encoding="utf-8")
        seen.append(claim_runtime_ids(gov, TASK, stamp, actor))
    _show(f"[件③ 同秒四次认领记录名] {[c for c, _s in seen]}")
    assert [c for c, _s in seen][1:] == [f"{first[0]}_02", f"{first[0]}_03", f"{first[0]}_04"]
    assert [s for _c, s in seen][1:] == [f"{first[1]}_02", f"{first[1]}_03", f"{first[1]}_04"]
    assert sorted(c for c, _s in seen) == [c for c, _s in seen]  # 文件名字典序 = 先后序
    assert nr.record_stamp(f"{seen[-1][0]}.md") == "20261103_071729"  # 读侧时刻段解析不受序号影响
    # 只有会话记录撞名也换号(两者同号)
    gov2 = tmp_path / "gov2"
    s_path = claim_record_paths(gov2, TASK, *first)[1]
    s_path.parent.mkdir(parents=True, exist_ok=True)
    s_path.write_text("x", encoding="utf-8")
    assert claim_runtime_ids(gov2, TASK, stamp, actor) == (f"{first[0]}_02", f"{first[1]}_02")
    assert CLAIM_ID_SEQ_MAX == 99


def test_acceptance3_same_second_reclaim_after_reopen_not_refused(f90_rig, monkeypatch):
    """验收③: 认领时刻钉在同一秒(旧轮认领与 reopen 后新一轮认领同秒), 新一轮认领不撞名、不被门拒, loop 续跑结案。"""
    from tools.aipos_cli import queue_mutation as qm

    fixed = qm.iso_z()
    monkeypatch.setattr(qm, "iso_z", lambda: fixed)
    r = f90_rig
    f282._blocked_card(r)
    rc, out, err = f282._reopen(r, f78.DRIVER)
    assert rc == 0, out + err
    out_io = io.StringIO()
    res = run_loop(TASK, r.gov, actor=f78.DRIVER, policy_id=f90.POLICY, out=out_io, watch=f90._agent_watch(r, []), interval=0.05,
                   max_wait=5, max_steps=30)
    text = out_io.getvalue()
    claims = sorted(p.name for p in (r.gov / "5_tasks" / "records" / "claims" / TASK).glob("claim_*.md"))
    sessions = sorted(p.name for p in (r.gov / "5_tasks" / "records" / "sessions" / TASK).glob("session_*.md"))
    _show(f"---- 验收③ 同秒(钉 {fixed})reopen 后重认领 loop outcome {res.outcome} ----\n" + f278._lines(text, "run claim", "done:")
          + f"\n[验收③ 认领记录] {claims}\n[验收③ 会话记录] {sessions}")
    assert "Claim record already exists" not in text and res.outcome == "completed", text
    assert len(claims) == 2 and claims[1] == claims[0][:-3] + "_02.md", claims
    recs = nr._read_task_records(r.gov, TASK)
    assert str(recs["latest_claim"]["claim_id"]).endswith("_02") and nr._claimer_instance(recs) == f78.EXEC


# ===========================================================================
# 件④ RepresenterError: 同进程先建记录写入器(看板测试先写过记录)再跑 F248 一键推荐 = 绿
# ===========================================================================

_ORDER_SCRIPT = r"""
import sys
sys.path.insert(0, {repo!r})
from tools.aipos_cli import record_writer as rw
assert "tools.aipos_cli.i18n" not in sys.modules, "前提: i18n 尚未导入"
rw._frontmatter_safe_dumper()  # 先建写入器(= 同进程更早的看板测试写过记录)
from tools.aipos_cli.i18n import Localized
value = Localized({{"zh": "标准 C4 待 Owner 抽查", "en": "Standard C4 awaiting the Owner's spot check"}}, "zh")
assert Localized not in rw._frontmatter_safe_dumper().yaml_representers, "前提: 子类表里无 Localized(F288 前即报错的加载顺序)"
text = rw._dump_frontmatter_yaml({{"title": value, "items": [value, {{"label": value}}]}})
assert type(value) is Localized and text.splitlines()[0] == "title: 标准 C4 待 Owner 抽查", text
import pytest
sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", {target!r}]))
"""


def test_item4_dumper_built_before_i18n_then_one_click_recommend_green(tmp_path):
    target = str(REPO_ROOT / "tests" / "test_aipos_f248_needs_you_one_click.py")
    script = _ORDER_SCRIPT.format(repo=str(REPO_ROOT), target=target)
    env = {k: v for k, v in os.environ.items() if k not in ("LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT", "LYBRA_LANG")}
    with fixture_procs.running([sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True) as proc:
        out, _ = proc.communicate(timeout=540)
    tail = "\n".join(out.strip().splitlines()[-3:])
    _show(f"---- 件④ 同进程: 先建写入器 → 再导入 i18n → 写双语值 → 跑 F248 一键推荐 exit {proc.returncode} ----\n{tail}")
    assert proc.returncode == 0 and "RepresenterError" not in out, out[-4000:]


def test_item4_single_write_entry_converts_str_subclasses():
    import inspect

    from tools.aipos_cli import record_writer as rw

    body = inspect.getsource(rw._dump_frontmatter_yaml)
    assert "_plain_scalars(ordered_meta)" in body  # 修在唯一序列化入口一处(两路写之前)
    assert "i18n" not in inspect.getsource(rw._plain_scalars).split('"""')[-1]  # 不让写入器依赖 i18n
