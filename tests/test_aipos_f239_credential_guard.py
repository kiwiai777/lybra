"""AIPOS-F239 — 产物入口凭据检查(10-26 chris 理想 HBLX-85 实撞)。

实撞: config 卡交回报告正文写入了测试凭据, 产物入口(artifact_ingest.validate_task_artifact)对凭据零判据, 只因同时撞程序判据被拒才没
入门; 交回 / 裁决报告入门后由 N6 收账提交并推送治理仓, 凭据进 git 历史后撤不回。

件① 凭据判据唯一声明(transitions artifact_ingest.credential_guard)+ 唯一实现(artifact_ingest.credential_scan / credential_hits):
    私钥块 / 常见令牌形 / Bearer 头 / 带口令的连接串 / 键值式口令 / 命令行口令参数逐类拒; 占位 / 打码 / 只提字段名 → 不拒;
    被 git 忽略的私有运行目录(规则 ≠ 报告落点自身的忽略规则)不扫。
件② 拒收与出口: 拒 INGEST_CREDENTIAL_IN_ARTIFACT, 拒因只给文件 + 行号 + 种类, 拒收记录 / CLI 输出 / loop 结束原文都不含值; pi 卡 loop
    同会话续接一次脱敏后入门; 审计报告同判, loop 续接审计会话(report_fix.artifact)。
件③ 存量只读检查: `lybra artifact credential-scan` 列命中清单(落点 / 文件 / 行 / 种类, 不含值), 不改任何文件。

靶场 = AIPOS-F90 / F95 / F196 / F236 同一靶场(tmp 治理根 + tmp 产品仓 + tmp HOME; 进程内门; 假 pi / 假 harness)。禁真门 / 真治理根 /
真工位 / 真 chris / 真 pi。夹具里的「凭据」一律是运行时拼出的明显假值(本文件不含任何真实凭据)。验收原文经 _show 打到 stdout。
"""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_aipos_f90_loop_one_stage as f90  # noqa: E402
import test_aipos_f221_root_ladder_fast_lane_rework as f221  # noqa: E402
import test_aipos_f78_engine_agnostic as f78  # noqa: E402
from fake_harness import fake_secret  # noqa: E402
from test_aipos_f131_loop_run_status import _cli  # noqa: E402
from test_aipos_f169_pi_model_declaration import _real_pi_template, install_fake_pi  # noqa: E402
from test_aipos_f171b_loop_single_instance_resume import _launch_rig, _records, _run  # noqa: E402
from test_aipos_f196_validation_to_pi import vrig  # noqa: E402,F401  — pi 靶场夹具
from test_aipos_f197_noncode_pi_completion import POL_DOCS, _docs_policy, _gov_card  # noqa: E402
from test_aipos_f236_return_rejection_exits import _config_card  # noqa: E402  — 只写治理根的 config 卡(HBLX-85 形态)
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402
from test_aipos_f78_engine_agnostic import DRIVER, _write  # noqa: E402
from test_aipos_f90_loop_one_stage import rig  # noqa: E402,F401  — pytest 夹具复用
from test_aipos_f95_harness_launch import lrig  # noqa: E402,F401  — 拉起靶场夹具
from tools.aipos_cli import artifact_ingest as ai  # noqa: E402
from tools.aipos_cli import loop_run_record as lrr  # noqa: E402
from tools.aipos_cli import next_resolver as nr  # noqa: E402
from tools.aipos_cli.loop_driver import artifact_fix_declaration, load_loop_contract, run_loop  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

CARD = f221.CARD
CRED = "INGEST_CREDENTIAL_IN_ARTIFACT"
SECRET = fake_secret()  # 运行时拼出的明显假值


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _samples() -> dict[str, tuple[str, str]]:
    """种类 → (一行夹具文本, 该行里的命中值); 值全部运行时拼出(明显假值)。"""
    body = "MIIEow" + "Qk7Lm2Zx9" * 6
    return {
        "private_key": ("-----BEGIN RSA PRIVATE KEY-----\n" + body + "\n-----END RSA PRIVATE KEY-----", body),
        "token_sk": ("OPENAI_KEY 取值 " + "sk-" + "Ab3Zq9" * 5, "sk-" + "Ab3Zq9" * 5),
        "token_github": ("推送用 " + "ghp_" + "Ab3D7kQ9" * 5, "ghp_" + "Ab3D7kQ9" * 5),
        "token_slack": ("通知 " + "xoxb-" + "1234-5678-AbCdEfGh", "xoxb-" + "1234-5678-AbCdEfGh"),
        "token_aws": ("访问 " + "AKIA" + "Q7RTZ2KD" * 2, "AKIA" + "Q7RTZ2KD" * 2),
        "token_jwt": ("cookie 值 " + "eyJ" + "hbGciOiJI" + ".eyJ" + "zdWIiOiIx" + "." + "SflKxwRJSMeK", "eyJ" + "hbGciOiJI"),
        "bearer_header": ("curl -H 'Authorization: Bearer " + SECRET * 2 + "' http://127.0.0.1:1/x", SECRET * 2),
        "url_credential": ("连接串 mysql://root:" + SECRET + "@db.local:3306/app", SECRET),
        "keyvalue_secret": ("db_password: " + SECRET, SECRET),
        "cli_secret_flag": ("redis-cli --password " + SECRET + " ping", SECRET),
    }


EXEMPT_LINES = [
    "password: <口令>", "password: ***", "token: [已打码]", "DB_PASSWORD=${DB_PASSWORD}", "api_key: changeme", "secret: $DB_SECRET",
    "password / passwd / pwd / 口令 字段只按名引用", "示例 mysql://user:pass@host/db", "Authorization: Bearer <token>", "密码: 见私有目录",
    "私钥块(BEGIN … PRIVATE KEY)只是叙述", "AWS 文档示例 AKIAIOSFODNN7EXAMPLE", "input_tokens: 12345", "dry_run_token: dr_abc_123",
    "pwd: /home/kiwi/work", "token = load_owner_token(connection_json)", "token: none", "口令: 已脱敏", "token 永不回显上屏",
    "凭据存放处: runtime/private/db-password(git 忽略)", "password: {password}", "export TOKEN=$(cat runtime/private/token)",
]


def _return_with(rig, extra: str) -> Path:
    path = f221._report(rig, "v1")
    path.write_text(path.read_text(encoding="utf-8") + "\n## 验证\n" + extra + "\n", encoding="utf-8")
    return path


# ===========================================================================
# 件① 逐类拒收(只含文件 / 行 / 种类, 无值); 占位 / 打码 / 只提字段名 → 不拒
# ===========================================================================

def test_item1_each_kind_rejected_without_value_and_exempt_lines_pass(rig):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    decl = ai.credential_guard_declaration()
    labels = {kind: label for kind, label, _rx, _low, _loose in decl["_patterns"]}
    for kind, (line, value) in _samples().items():
        path = _return_with(rig, line)
        got = ai.validate_task_artifact(CARD, rig.gov)
        text = "\n".join(got["reasons"])
        _show(f"---- 件① {kind}: ok={got['ok']} category={got['category']} ----\n{text}")
        assert not got["ok"] and got["category"] == CRED, (kind, got)
        assert value not in text and value not in json.dumps(got, ensure_ascii=False), f"{kind}: 拒因不得回显命中值"
        hit_line = path.read_text(encoding="utf-8").splitlines().index(line.splitlines()[0]) + 1
        assert f"task_cards/{CARD}/RETURN.md 第 {hit_line} 行({labels[kind]})" in text, (kind, text)
        assert {h["kind"] for h in got["credential_hits"]} >= {kind}
        assert all(set(h) == {"path", "line", "kind", "label"} for h in got["credential_hits"]), "命中清单不带值字段"
    path = _return_with(rig, "\n".join(EXEMPT_LINES))
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"---- 件① 占位 / 打码 / 只提字段名({len(EXEMPT_LINES)} 行): ok={got['ok']} category={got['category']} ----\n"
          + "\n".join(EXEMPT_LINES))
    assert got["ok"], got
    # 中文键 + 全角冒号同判
    _return_with(rig, "测试账号密码：" + SECRET)
    got = ai.validate_task_artifact(CARD, rig.gov)
    assert got["category"] == CRED and "键值式口令" in got["reasons"][0] and SECRET not in got["reasons"][0]


def test_item1_artifacts_and_output_target_scanned_code_card_output_target_not(rig, tmp_path):  # noqa: F811
    _config_card(rig, output_target=f"task_cards/{CARD}/artifacts/, governance/runbook/")
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    _write(rig.gov / "task_cards" / CARD / "artifacts" / "login.log", "POST /login password=" + SECRET + "\n")
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[件① artifacts/ 下任意文本文件] {got['category']}: {got['reasons'][0][:300]}")
    assert got["category"] == CRED and f"task_cards/{CARD}/artifacts/login.log 第 1 行" in got["reasons"][0]
    (rig.gov / "task_cards" / CARD / "artifacts" / "login.log").unlink()
    _write(rig.gov / "governance" / "runbook" / "deploy.md", "# 部署\n\nadmin token: " + SECRET + "\n")
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[件① output_target(lane 为治理根)报告落点外] {got['category']}: {got['reasons'][0][:300]}; files={got['credential_files']}")
    assert got["category"] == CRED and "governance/runbook/deploy.md 第 3 行" in got["reasons"][0]
    assert got["credential_files"] == [str((rig.gov / "governance" / "runbook" / "deploy.md").resolve())]
    # 代码卡(lane 仓 = 产品仓)的 output_target 是产品仓路径 = 不扫(不扫产品代码仓)
    code_fm = {"task_id": "X", "lane": {"repo": str(rig.code_repo)}, "output_target": "governance/runbook/"}
    scan = ai.credential_scan(rig.gov, rig.gov / "task_cards" / "X", card_fm=code_fm)
    assert scan["hits"] == [], scan


# ===========================================================================
# 件① 被 git 忽略的私有运行目录不扫; 未被忽略的 runtime/*.json 照扫; 整个落点被忽略的项目照扫
# ===========================================================================

def test_item2_gitignored_private_dir_not_rejected(rig):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    f221._report(rig, "v1")
    slot = rig.gov / "task_cards" / CARD
    _write(slot / ".gitignore", "runtime/private/\n")
    _write(slot / "runtime" / "private" / "accounts.json", json.dumps({"password": SECRET, "token": SECRET * 2}))
    _write(slot / "runtime" / "private" / "my.cnf", "[client]\npassword=" + SECRET + "\n")
    _write(slot / "runtime" / "log" / "app.log", "token=" + SECRET + "\n")  # *.log 不在 slot_globs(运行日志不入门判据)
    got = ai.validate_task_artifact(CARD, rig.gov)
    scan = ai.credential_scan(rig.gov, slot, report=slot / "RETURN.md")
    _show(f"---- 件② 私有目录(落点 .gitignore runtime/private/)含凭据: ok={got['ok']} category={got['category']} "
          f"扫描 {scan['files']} 个文件, 私有跳过 {scan['ignored']} 个 ----")
    assert got["ok"], got
    assert scan["ignored"] == 2 and scan["hits"] == []
    _write(slot / "runtime" / "state.json", json.dumps({"api_key": SECRET}))
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[件② 未被忽略的 runtime/state.json] {got['category']}: {got['reasons'][0][:240]}")
    assert got["category"] == CRED and f"task_cards/{CARD}/runtime/state.json 第 1 行" in got["reasons"][0]
    (slot / "runtime" / "state.json").unlink()
    # 整个落点都被忽略(lybra task_cards/ 形态): 忽略不带「私有」语义 = 全扫; 报告本身永远照扫
    _write(rig.gov / ".gitignore", "task_cards/\n")
    subprocess.run(["git", "-C", str(rig.gov), "rm", "-r", "-q", "--cached", "task_cards"], check=True, capture_output=True)
    _write(slot / "artifacts" / "note.md", "token: " + SECRET + "\n")
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[件② 整个落点被忽略的项目] {got['category']}: {got['reasons'][0][:240]}")
    assert got["category"] == CRED and "artifacts/note.md 第 1 行" in got["reasons"][0]
    assert "runtime/private/accounts.json" in got["reasons"][0], "落点整体被忽略: git 不读落点内 .gitignore, 忽略不带私有语义 = 照扫"


def test_item2_not_a_git_repo_scans_everything(rig):  # noqa: F811
    """治理根不是 git 仓 = 全扫(fail-closed): 私有目录同样被扫。"""
    _config_card(rig)
    f221._report(rig, "v1")
    slot = rig.gov / "task_cards" / CARD
    _write(slot / ".gitignore", "runtime/private/\n")
    _write(slot / "runtime" / "private" / "accounts.json", json.dumps({"password": SECRET}))
    assert not (rig.gov / ".git").exists()
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[件② 非 git 仓 fail-closed] {got['category']}: {got['reasons'][0][:240]}")
    assert got["category"] == CRED and "runtime/private/accounts.json" in got["reasons"][0]


# ===========================================================================
# 件② 真交回拒收: CLI 原文 / 拒收记录 / next 均不含值; 改后照常交回
# ===========================================================================

def test_item2_real_return_rejected_record_and_next_without_value(rig):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    _return_with(rig, "- 登录验证: curl -d \"username=demo&password=" + SECRET + "\" https://127.0.0.1:8443/login")
    rc, out, err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov)])
    _show(f"---- 件② 真交回: lybra artifact ingest exit {rc} ----\n{out}{err}")
    assert rc != 0 and CRED in out + err and SECRET not in out + err
    records = ai.return_rejection_records(rig.gov, CARD)
    assert len(records) == 1 and records[0]["category"] == CRED
    raw = Path(records[0]["_path"]).read_text(encoding="utf-8")
    _show(f"---- 件② 交回拒收记录 {Path(records[0]['_path']).relative_to(rig.gov)} ----\n{raw[:1200]}")
    assert SECRET not in raw
    d = nr.derive_next_step(CARD, rig.gov)
    text = nr.format_output(d)
    _show("---- 件② 拒后 lybra next 原文 ----\n" + text[:1500])
    assert d["action"]["type"] == "return_rejected" and SECRET not in text and SECRET not in json.dumps(d, ensure_ascii=False)
    # 脱敏(值换占位 + 写明存放处)后 = 指纹变, 恢复给交回命令, 入门通过
    path = rig.gov / "task_cards" / CARD / "RETURN.md"
    path.write_text(path.read_text(encoding="utf-8").replace(SECRET, "<口令>") + "\n凭据存放处: runtime/private/login-password\n",
                    encoding="utf-8")
    d = nr.derive_next_step(CARD, rig.gov)
    assert d["derivable"] and d["command"].startswith(f"lybra artifact ingest --task-id {CARD} --kind return"), d
    rc, out, err = _cli(["artifact", "ingest", "--task-id", CARD, "--kind", "return", "--workspace-root", str(rig.gov)])
    _show(f"[件② 脱敏后重交] exit {rc}")
    assert rc == 0, out + err


def test_item2_loop_subagent_card_ends_return_rejected_message_without_value(rig):  # noqa: F811
    _config_card(rig, "pending")
    init_governance_repo(rig.gov)

    def executor(r):
        _return_with(r, "- 登录验证: password=" + SECRET)

    res, text = f221._loop(rig, [], executor)
    _show(f"---- 件② 子 agent 卡交回含凭据: loop exit {res.exit_code} reason {res.reason} ----\n{res.message[:2500]}")
    assert res.reason == "return_rejected" and CRED in res.message
    assert SECRET not in res.message and SECRET not in text, "loop 结束原文与日志不含值"
    note = artifact_fix_declaration(load_loop_contract())["category_notes"][CRED]
    assert note in res.message, "修产物指令附凭据类补充指令(换占位 + 写明存放处)"
    log = Path(_records(rig.gov, CARD)[-1]["log_path"]).read_text(encoding="utf-8")
    assert SECRET not in log


# ===========================================================================
# 件② / 验收③ pi 卡: loop 同会话续接执行体脱敏一次 → 重新入门; 不改 = return_rejected
# ===========================================================================

def _pi_docs(vrig, monkeypatch, mode: str):
    _real_pi_template(monkeypatch)
    install_fake_pi(vrig.home / "fakepi", monkeypatch)
    _docs_policy(vrig.gov)
    _gov_card(vrig.gov, CARD)
    init_governance_repo(vrig.gov)
    monkeypatch.setenv("FAKE_EXEC_MODE", mode)
    out = io.StringIO()
    res = run_loop(CARD, vrig.gov, actor=DRIVER, policy_id=POL_DOCS, out=out, interval=0.05, max_wait=60, max_steps=20)
    return res, out.getvalue()


def _fixes(gov: Path) -> list[dict]:
    paths = sorted((gov / "5_tasks" / "records" / "loop_runs" / CARD).glob("looprun_*.md"))
    return [f for p in paths for f in nr._read_frontmatter(p).get("report_fixes") or []]


def _gate_returns(gov: Path) -> list[Path]:
    directory = gov / "5_tasks" / "records" / "returns" / CARD
    return sorted(directory.glob("*.md")) if directory.is_dir() else []


def test_item3_pi_card_resumed_once_redacts_then_admitted(vrig, monkeypatch):
    res, text = _pi_docs(vrig, monkeypatch, "docs_cred_once")
    lines = [ln for ln in text.splitlines() if any(k in ln for k in ("report_fix:", "交回被产物入口拒收", "run return", "] ok:", "exit "))]
    _show(f"---- 验收③ pi 卡交回含凭据 → 同会话续接脱敏 → 重新入门: loop exit {res.exit_code} ----\n" + "\n".join(lines))
    prompt = (vrig.log / f"{CARD}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[验收③ 续接修产物指令原文] {prompt[:1800]}")
    assert f"被产物入口拒收 {CRED}" in prompt and SECRET not in prompt and "换成占位" in prompt and "存放处" in prompt
    assert [(f["role"], f["outcome"], f["kinds"]) for f in _fixes(vrig.gov)] == [("executor", "resumed", [CRED])]
    report = (vrig.gov / "task_cards" / CARD / "RETURN.md").read_text(encoding="utf-8")
    assert SECRET not in report and "<口令>" in report and "302(登录成功)" in report, "值换占位, 验证结论保留"
    assert _gate_returns(vrig.gov), "脱敏后重新入门, 门落交回记录"
    assert SECRET not in text


def test_item3_pi_card_not_redacted_ends_return_rejected(vrig, monkeypatch):
    res, text = _pi_docs(vrig, monkeypatch, "docs_cred")
    _show(f"---- 验收③ pi 卡续接后未改: loop exit {res.exit_code} reason {res.reason} ----\n{res.message[:1500]}")
    assert res.reason == "return_rejected" and "续接后报告未改写" in res.message and SECRET not in res.message + text
    assert [(f["outcome"], f["kinds"]) for f in _fixes(vrig.gov)] == [("not_rewritten", [CRED])]
    assert not _gate_returns(vrig.gov)


# ===========================================================================
# 验收④ 审计报告含凭据 → 裁决入门拒收; loop 续接审计会话脱敏一次; 不改 = audit_report_rejected(advisor_fix)
# ===========================================================================

def _audit_fixes(gov: Path) -> list[dict]:
    return [f for r in _records(gov) for f in r.get("report_fixes") or []]


def test_item4_audit_report_credential_rejected_then_resumed(lrig, monkeypatch):
    import test_aipos_f95_harness_launch as f95

    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, f95.TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "cred_once")
    res, text = _run(lrig)
    lines = [ln for ln in text.splitlines() if re.search(r"report_fix:|门拒 @|审计报告被裁决入口拒收|exit \d|done:", ln)]
    _show(f"---- 验收④ 审计报告含凭据 → 裁决入门拒 → 同会话续接脱敏: loop exit {res.exit_code} ----\n" + "\n".join(lines))
    assert res.exit_code == 0 and res.outcome == "completed", text
    reject = next(ln for ln in text.splitlines() if "门拒 @ verdict" in ln or CRED in ln)
    assert CRED in text and SECRET not in text
    prompt = (lrig.log / f"{f95.AUDIT}.resume1.prompt").read_text(encoding="utf-8")
    _show(f"[验收④ 裁决拒收原文行] {reject[:600]}\n[验收④ 续接修审计报告指令原文] {prompt[:1500]}")
    assert prompt.startswith("你的审计报告 ") and f"被裁决入口拒收 {CRED}" in prompt and SECRET not in prompt
    assert [(f["role"], f["outcome"], f["kinds"]) for f in _audit_fixes(lrig.gov)] == [("auditor", "resumed", [CRED])]
    report = nr.auditor_artifact_watch(lrig.gov, f95.AUDIT)
    body = (report[0] / report[1][0]).read_text(encoding="utf-8")
    assert SECRET not in body and "[已打码]" in body and "verdict: PASS" in body, "裁决结论不变, 值换打码"


def test_item4_audit_report_not_fixed_ends_advisor_fix(lrig, monkeypatch):
    import test_aipos_f95_harness_launch as f95

    _launch_rig(lrig, monkeypatch)
    f90._card(lrig.gov, f95.TASK, "pending", harness="pi")
    init_governance_repo(lrig.gov)
    monkeypatch.setenv("FAKE_AUDIT_MODE", "cred_stay")
    res, text = _run(lrig)
    _show(f"---- 验收④ 审计体续接后未改: loop exit {res.exit_code} reason {res.reason} ----\n{res.message[:1800]}")
    assert res.reason == "audit_report_rejected" and res.outcome == "gate_rejected", text
    assert "修报告指令(会话不可续接时顾问派给本卡审计实例): 你的审计报告 " in res.message and SECRET not in res.message + text
    assert [(f["role"], f["outcome"]) for f in _audit_fixes(lrig.gov)] == [("auditor", "not_rewritten")]
    status = lrr.loop_status(lrig.gov, f95.TASK)
    na = status["next_action"]
    _show(f"[验收④ loop status 下一动作] action={na['action']} reason={na['reason']}\n{na['hint'][:1200]}")
    assert na["action"] == "advisor_fix" and na["reason"] == "audit_report_rejected"
    assert "lybra artifact ingest --task-id <审计卡ID> --kind verdict" in na["hint"] and SECRET not in na["hint"]
    # 裁决入口预演原文(只读)
    rc, out, err = _cli(["artifact", "ingest", "--task-id", f95.AUDIT, "--kind", "verdict", "--workspace-root", str(lrig.gov), "--dry-run"])
    _show(f"---- 验收④ 审计报告含凭据: lybra artifact ingest --kind verdict --dry-run exit {rc} ----\n{out}{err}")
    assert rc != 0 and CRED in out + err and SECRET not in out + err


# ===========================================================================
# 件③ / 验收⑤ 存量只读检查(夹具治理根; 对真 lybra 治理根的只读跑在 RETURN 证据)
# ===========================================================================

def test_item5_stock_scan_lists_hits_without_values_read_only(rig):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    _return_with(rig, "token: " + SECRET + "\n" + "Authorization: Bearer " + SECRET * 2)
    clean = rig.gov / "task_cards" / "CLEAN-1"
    _write(clean / "RETURN.md", "# RETURN\n\npassword: <口令>\n")
    snap = rig.gov / "5_tasks" / "records" / "audit_verdicts" / CARD / "report_snapshots" / "verdict_x.md"
    _write(snap, "---\nrecord_type: audit_report_snapshot\n---\n# 审计\n\nmysql://root:" + SECRET + "@db/app\n")
    before = {p: p.stat().st_mtime_ns for p in rig.gov.rglob("*") if p.is_file() and ".git" not in p.parts}
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "artifact", "credential-scan"])
    _show(f"---- 件③ lybra artifact credential-scan exit {rc} ----\n{out}{err}")
    assert rc == 1 and SECRET not in out + err
    assert "命中 3 处, 涉及 2 个报告落点; 按种类: bearer_header 1, keyvalue_secret 1, url_credential 1" in out
    assert f"task_cards/{CARD}/RETURN.md 第 " in out and f"5_tasks/records/audit_verdicts/{CARD}/report_snapshots/verdict_x.md 第 6 行" in out
    rc, out, err = _cli(["--workspace-root", str(rig.gov), "artifact", "credential-scan", "--json"])
    data = json.loads(out)
    assert rc == 1 and data["by_kind"] == {"bearer_header": 1, "keyvalue_secret": 1, "url_credential": 1} and SECRET not in out
    assert all(set(h) == {"slot", "path", "line", "kind", "label"} for h in data["hits"])
    after = {p: p.stat().st_mtime_ns for p in rig.gov.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert before == after, "只读: 不改任何文件"
    for p in (rig.gov / "task_cards" / CARD / "RETURN.md", snap):
        p.unlink()
    rc, out, _err = _cli(["--workspace-root", str(rig.gov), "artifact", "credential-scan"])
    _show(f"[件③ 清理后] exit {rc}\n{out}")
    assert rc == 0 and "无命中" in out


# ===========================================================================
# 声明: 唯一读取口 fail-closed; 缺本节 = 不查; 无绕过开关
# ===========================================================================

def test_declaration_fail_closed_and_missing_means_no_check(rig, monkeypatch):  # noqa: F811
    _config_card(rig)
    init_governance_repo(rig.gov)
    _return_with(rig, "token: " + SECRET)
    real = nr._artifact_ingest_declaration()
    broken = json.loads(json.dumps(real))
    broken["credential_guard"]["patterns"][0]["regex"] = "(unclosed"
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: broken)
    with pytest.raises(SchemaLoadError, match="credential_guard"):
        ai.credential_guard_declaration()
    got = ai.validate_task_artifact(CARD, rig.gov)
    _show(f"[声明坏 = fail-closed] {got['category']}: {got['reasons'][0][:200]}")
    assert got["category"] == "INGEST_DECLARATION_MISSING"
    broken = json.loads(json.dumps(real))
    del broken["credential_guard"]["texts"]["refusal"]
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: broken)
    with pytest.raises(SchemaLoadError, match="texts"):
        ai.credential_guard_declaration()
    missing = {k: v for k, v in real.items() if k != "credential_guard"}
    monkeypatch.setattr(nr, "_artifact_ingest_declaration", lambda: missing)
    assert ai.credential_guard_declaration() is None and ai.validate_task_artifact(CARD, rig.gov)["ok"], "缺本节 = 不查(行为不变)"
    decl = real["credential_guard"]
    assert set(decl) == {"description", "category", "scan", "patterns", "exempt", "texts"}, "无绕过开关, 豁免只经声明表"
    assert decl["category"] == CRED


def test_loop_declarations_single_source():
    contract = load_loop_contract()
    exe = artifact_fix_declaration(contract)
    aud = artifact_fix_declaration(contract, "auditor")
    assert CRED in exe["categories"] and CRED in aud["categories"]
    assert aud["end_reason"] == "audit_report_rejected" and "audit_report_rejected" in contract["run_record"]["end_reasons"]
    from tools.aipos_cli.verb_contract import declared_exit_codes

    assert declared_exit_codes("lybra_artifact_credential_scan") == {"clean": 0, "hits": 1, "error": 2}
    bad = json.loads(json.dumps(contract))
    bad["launch"]["report_fix"]["artifact"]["category_notes"] = {CRED: ""}
    with pytest.raises(SchemaLoadError, match="category_notes"):
        artifact_fix_declaration(bad, "auditor")
    del bad["launch"]["report_fix"]["artifact"]
    with pytest.raises(SchemaLoadError, match="report_fix.artifact"):
        artifact_fix_declaration(bad, "auditor")
