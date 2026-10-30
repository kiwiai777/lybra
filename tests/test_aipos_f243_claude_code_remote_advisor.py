"""AIPOS-F243 — Claude Code 顾问会话可登记在治理根以外的机器; 他机会话经拉取出口取章程与技能; codex ↔ claude-code 同实例来回切换。

件① 声明: distribution.schema harness_semantics.kinds.claude-code.harness_host = optional(同 codex 语义; 他机会话 dir 如实记、不校验本机
    存在; 本机会话规则与记录形不变)。
件② 他机 claude-code 会话 sync 零写入, 章程(advisor-charter-claude-code)与技能(advisor-skills)两件都给拉取出口(remote_session_delivery
    + 其 land / skills 段; 拼装唯一 charter_render.charter_pull_command / skills_pull_command), 出口带落点相对路径, 由会话开局自己落盘;
    技能件新只读导出出口 `lybra skills`(tar 流, 文件集合 = 门清单同一构建器)。
件③ `lybra roles redeclare` / enroll / enroll-deliver 的 claude-code 他机路径放行; 授权分档沿 F235 不改(授权外 = Owner 收件箱)。
件④ 下游: onboarding guide 他机 claude-code 顾问(第 5 步给章程落盘与技能拉取命令)等。

靶场: 件①③ = AIPOS-F235 chris 形临时治理根(人肉期样本: 顾问实例槽 codex@他机; 不连门); 件②④ = AIPOS-F92 靶场 probe_range
(临时 HOME / 临时 home 根 / 自起临时门, 向导原样走到第 4 步), 「他机」= 假 ssh(靶场 bin 下的 ssh 只认声明的门机别名, 把引号内命令
在本机执行, 模拟会话经 ssh 到治理根所在机)。禁真门 / 真治理根 / 真工位 / 真 pi; 不写 chris-huibojin 治理根。

验收(卡面 ★验收):
 ① codex@他机 → claude-code@他机 redeclare 预演 / --confirm 原文(授权外进收件箱, 批准后生效), 再切回 codex 原文
 ② 他机 claude-code 实例 sync --dry-run 零写入并给章程与技能两个拉取出口原文(含落点相对路径); 按出口执行后会话目录得到与本机 sync
    一致的 .claude/rules/lybra-advisor.md 与 .claude/skills(内容比对)
 ③ 本机 claude-code 与 codex 既有行为不变(本文件末段 + F129 / F136 / F138 / F92 / F235 回归)
"""
from __future__ import annotations

import io
import json
import os
import shlex
import socket
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from test_aipos_f92_onboarding_walkthrough import Range, _write, probe_range  # noqa: E402,F401  — 靶场构件唯一来源
from test_aipos_f136_advisor_follow_through import _run_guide_to_step5  # noqa: E402  — 向导走法唯一来源
from test_aipos_f235_instance_redeclare import (  # noqa: E402  — chris 形改登记靶场唯一来源
    ADV_LANTU,
    ADV_OTA,
    MAC,
    MAC_DIR,
    _log_lines,
    _redeclare,
    _rig,
    _role_doc,
)

LOCAL = socket.gethostname()
GATE_ALIAS = "dev-gate"
REMOTE_SESSION = "/Users/probe/lybra-probe"  # 他机会话目录(本机不存在, 如实登记)


def _show(text: str) -> None:
    print(text, flush=True)


def _lybra_bytes(*argv: str, cwd: Path | None = None) -> tuple[int, bytes, str]:
    """子进程跑 lybra(stdout 按字节取: `lybra skills` 输出 tar 流); 环境 = 当前进程(靶场 monkeypatch 的临时 HOME)。"""
    code = ("import sys\nsys.path.insert(0, %r)\nfrom tools.aipos_cli.aipos_cli import main\nsys.exit(main())\n" % str(REPO_ROOT))
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    p = subprocess.run([sys.executable, "-c", code, *argv], cwd=str(cwd or REPO_ROOT), env=env, capture_output=True, timeout=300)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def _declared_skill_members(role: str = "advisor", kind: str = "claude-code") -> dict[str, bytes]:
    """声明给该角色该 harness 的技能件 → {落点相对路径: 母本字节}(门清单同一构建器)。"""
    from tools.aipos_cli.distribution_sync import harness_distributions
    from tools.aipos_cli.workstation_wiring import declared_role_distributions

    out: dict[str, bytes] = {}
    for d in harness_distributions(declared_role_distributions(role, role), kind):
        if d.get("kind") == "skills":
            for f in d["files"]:
                out[f"{d['target_path'].strip('/')}/{f['path']}"] = (REPO_ROOT / d["source_path"] / f["path"]).read_bytes()
    assert out, "声明里应有给 claude-code 顾问的技能件"
    return out


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _approve_pending(gov: Path, *, words: str) -> str:
    from tools.aipos_cli.owner_inbox import collect_project_inbox, decide

    items = [i for i in collect_project_inbox(gov, home_root=gov.parent)["items"] if i["category"] == "enrollment_redeclare"]
    assert len(items) == 1, items
    report = decide(items[0]["item_id"], "approve_redeclare", home_root=gov.parent, relayed_by=ADV_OTA, owner_words=words, confirm=True)
    assert report["ok"], report
    return str(items[0]["decision_id"])


# ===========================================================================
# 件①③ codex@他机 → claude-code@他机(授权外 → Owner 收件箱 → 批准生效)→ 再切回 codex@他机
# ===========================================================================

def test_item1_redeclare_codex_remote_to_claude_code_remote_and_back(tmp_path, monkeypatch):
    from tools.aipos_cli.distribution_sync import workstation_harness
    from tools.aipos_cli.enrollment import registered_harness, registration_drift

    gov = _rig(tmp_path, monkeypatch, launch=["pi"])  # 项目授权拉起方式只有 pi: 换 harness = 授权外(第一档)
    reg0 = registered_harness(gov, ADV_LANTU)
    _show(f"[①·人肉期样本现登记] {reg0['kind']}@{reg0['host']}:{reg0['dir']} 来源 {reg0['source']}")
    assert (reg0["kind"], reg0["host"], reg0["dir"]) == ("codex", MAC, MAC_DIR)
    to_cc = ("--harness", "claude-code", "--host", MAC, "--dir", MAC_DIR)

    before = _log_lines(gov)
    rc, out, err = _redeclare(gov, *to_cc)
    _show(f"---- ①·codex@{MAC} → claude-code@{MAC} 预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and "kind: codex → claude-code" in out
    assert "host:" not in out.split("现登记")[1].split("授权")[0] and "dir:" not in out.split("现登记")[1].split("授权")[0], "同机同目录只换 kind"
    assert "第一档" in out and "harness:claude-code" in out and _log_lines(gov) == before, "预演零写入"
    rc, out, err = _redeclare(gov, *to_cc, confirm=True)
    _show(f"---- ①·--confirm 原文(授权外 → 收件箱)rc={rc} ----\n{out}{err}")
    assert rc == 3 and " redeclare_request " in _log_lines(gov)[-1] and registered_harness(gov, ADV_LANTU)["kind"] == "codex", "请求态 = 未生效"
    decision = _approve_pending(gov, words="理想顾问改用 Mac 上的 Claude Code, 批准")
    rc, out, err = _redeclare(gov, *to_cc, confirm=True)
    _show(f"---- ①·Owner 批准后 --confirm 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "Owner 已批准" in out and "已写入" in out
    line = _log_lines(gov)[-1]
    _show(f"[①·新位置事件] {line}")
    assert (f"harness=claude-code harness_host={MAC} harness_dir={MAC_DIR} from_harness=codex from_host={MAC} from_dir={MAC_DIR} "
            f"authorized_by=owner:{decision}") in line
    slot = _role_doc(gov)["instances"][ADV_LANTU]
    _show(f"[①·实例槽] {json.dumps(slot['harness'], ensure_ascii=False)}")
    assert slot["harness"] == {"kind": "claude-code", "dir": MAC_DIR, "host": MAC}
    reg = registered_harness(gov, ADV_LANTU)
    assert (reg["kind"], reg["host"], reg["dir"], reg["source"]) == ("claude-code", MAC, MAC_DIR, "event")
    assert registration_drift(gov) == []
    ws = workstation_harness(gov, ADV_LANTU)
    assert (ws["kind"], ws["host"], str(ws["dir"]), ws["local"]) == ("claude-code", MAC, MAC_DIR, False), "他机会话 = 本机无落点"

    # 件② 只读出口(不连门): 他机 claude-code 实例的章程(--body-only 无横幅)与技能 tar 流
    rc, body, err = _lybra_bytes("charter", "--role", "advisor", "--instance", ADV_LANTU, "--workspace-root", str(gov), "--body-only")
    assert rc == 0 and err == "" and body.decode("utf-8").startswith("# ") and "<!-- lybra:charter-render" in body.decode("utf-8")
    rc, blob, err = _lybra_bytes("skills", "--role", "advisor", "--instance", ADV_LANTU, "--workspace-root", str(gov))
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        got = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    _show(f"[②·lybra skills(他机 claude-code)] exit {rc}, tar 成员 {len(got)} 个, 前 3: {sorted(got)[:3]}")
    assert rc == 0 and err == "" and got == _declared_skill_members()
    rc, blob, err = _lybra_bytes("skills", "--role", "advisor", "--instance", ADV_OTA, "--workspace-root", str(gov))
    _show(f"[②·lybra skills(codex 实例)拒] exit {rc}: {err.strip()}")
    assert rc == 3 and blob == b"" and "harness=codex 下无技能分发条目" in err

    # 切回 codex(同主机同目录; 一次切换一次改登记; 授权外同样进收件箱)
    to_codex = ("--harness", "codex", "--host", MAC, "--dir", MAC_DIR)
    rc, out, err = _redeclare(gov, *to_codex)
    _show(f"---- ①·claude-code@{MAC} → codex@{MAC} 预演原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "kind: claude-code → codex" in out and "第一档" in out and "harness:codex" in out
    assert _redeclare(gov, *to_codex, confirm=True)[0] == 3
    decision2 = _approve_pending(gov, words="Codex 额度恢复, 切回 Codex, 批准")
    rc, out, err = _redeclare(gov, *to_codex, confirm=True)
    _show(f"---- ①·切回 codex --confirm 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "Owner 已批准" in out
    assert (f"harness=codex harness_host={MAC} harness_dir={MAC_DIR} from_harness=claude-code from_host={MAC} from_dir={MAC_DIR} "
            f"authorized_by=owner:{decision2}") in _log_lines(gov)[-1]
    assert _role_doc(gov)["instances"][ADV_LANTU]["harness"] == {"kind": "codex", "dir": MAC_DIR, "host": MAC}
    reg = registered_harness(gov, ADV_LANTU)
    assert (reg["kind"], reg["host"], reg["dir"]) == ("codex", MAC, MAC_DIR) and registration_drift(gov) == []


def test_item3_redeclare_within_authorization_is_tier2(tmp_path, monkeypatch):
    """授权内(拉起方式含 claude-code、主机含 Mac)= 第二档, 本实例自己可做(F235 分档规则不改)。"""
    gov = _rig(tmp_path, monkeypatch, launch=["pi", "claude-code", "codex"])
    rc, out, err = _redeclare(gov, "--harness", "claude-code", "--host", MAC, "--dir", MAC_DIR, confirm=True)
    _show(f"---- ③·授权内 codex@{MAC} → claude-code@{MAC} 原文 rc={rc} ----\n{out}{err}")
    assert rc == 0 and "第二档" in out and "auth_chris-demo@r1" in out and "已写入" in out
    assert "authorized_by=auth_chris-demo@r1" in _log_lines(gov)[-1]
    rc, out, err = _redeclare(gov, "--harness", "codex", "--host", MAC, "--dir", MAC_DIR, confirm=True)
    assert rc == 0 and "第二档" in out and _role_doc(gov)["instances"][ADV_LANTU]["harness"]["kind"] == "codex"


# ===========================================================================
# 件②④ 他机 claude-code 顾问: 向导 → sync 拉取出口(零写入)→ 照出口在「Mac 会话目录」落盘 → 与本机 sync 内容一致
# ===========================================================================

def _fake_ssh(r: Range) -> None:
    """靶场「他机」: ssh 只认声明的门机别名, 把引号内命令在本机(治理根所在机)执行; 其余主机 = 255(同 ssh 连不上)。"""
    _write(r.bin / "ssh", f'#!/bin/bash\n[ "$1" = {GATE_ALIAS} ] || {{ echo "ssh: unknown host $1" >&2; exit 255; }}\nshift\nexec bash -c "$*"\n')
    (r.bin / "ssh").chmod(0o755)


def test_item2_remote_claude_code_pull_exits_land_same_as_local_sync(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    guide, enroll_out = _run_guide_to_step5(
        r, f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness claude-code --advisor-host mac-probe "
           f"--advisor-dir {REMOTE_SESSION} --envelope-days 7 --max-tasks 20")
    inst = guide["instances"]["advisor"]
    _show(f"[④·guide 他机 claude-code 第 4 步 enroll-deliver 输出]\n{enroll_out}")
    assert inst == "advisor.lybra-probe.mac-probe"
    role = json.loads((gov / ".lybra" / "role").read_text(encoding="utf-8"))
    assert role["harness"] == {"kind": "claude-code", "dir": REMOTE_SESSION, "host": "mac-probe"}
    assert not Path(REMOTE_SESSION).exists() and not (gov / ".claude").exists() and not (r.session / ".claude").exists()
    step5 = guide["steps"][4]
    _show("---- ④·guide 第 5 步 command 原文(他机 claude-code)----\n" + step5["command"])
    assert ".claude/rules/lybra-advisor.md" in step5["command"] and "| tar -xf -" in step5["command"]
    assert f"{r.bin}/lybra skills --role advisor --instance {inst}" in step5["command"]  # AIPOS-F244 件①: 稳定入口绝对路径

    # 声明他机视角治理根主机(既有两阶段写口)后出口带真实主机
    r.sh(f"lybra project set-workstation lybra-probe --instance {inst} --gate-ssh-alias {GATE_ALIAS} "
         f"--material-access '经 ssh {GATE_ALIAS} 读写治理根' --home-root {r.hroot} --confirm")
    snap = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(gov.rglob("*")) if p.is_file() and ".git" not in p.parts}
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run")
    _show("---- ②·他机 claude-code sync --dry-run 原文 ----\n" + out)
    res = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))["workstations"][0]["result"]
    snap2 = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(gov.rglob("*")) if p.is_file() and ".git" not in p.parts}
    assert snap2 == snap, "dry-run 零写入"
    assert res["undelivered"] == [] and res["files_fetched"] == 0 and res["plan"] == []
    pulls = {p["distribution_id"]: p for p in res["pull"]}
    _show("[②·pull 段]\n" + json.dumps(res["pull"], ensure_ascii=False, indent=1))
    assert set(pulls) == {"advisor-charter-claude-code", "advisor-skills"}
    charter_pull, skills_pull = pulls["advisor-charter-claude-code"], pulls["advisor-skills"]
    assert charter_pull["target_path"] == ".claude/rules/lybra-advisor.md" and skills_pull["target_path"] == ".claude/skills/"
    land = (f"mkdir -p .claude/rules && ssh {GATE_ALIAS} "
            + shlex.quote(f"cd {gov} && {r.bin}/lybra charter --role advisor --instance {inst} --body-only")
            + " > .claude/rules/lybra-advisor.md.tmp && mv .claude/rules/lybra-advisor.md.tmp .claude/rules/lybra-advisor.md")
    skills = f"ssh {GATE_ALIAS} " + shlex.quote(f"cd {gov} && {r.bin}/lybra skills --role advisor --instance {inst}") + " | tar -xf -"
    assert charter_pull["land_command"] == land and skills_pull["command"] == skills
    assert f"`{land}`" in out and f"`{skills}`" in out and ".claude/rules/lybra-advisor.md" in out and ".claude/skills/" in out
    assert not (gov / ".claude").exists() and not (r.session / ".claude").exists(), "他机会话: 本机零落盘(只给出口)"

    # 照出口在「Mac 会话目录」执行(会话开局自己落盘)
    _fake_ssh(r)
    mac = r.tmp / "mac-session"
    mac.mkdir()
    r.sh(land, cwd=mac)
    r.sh(skills, cwd=mac)
    r.sh(land, cwd=mac)  # 再跑一次: 幂等覆盖, 不留 .tmp
    pulled = _tree(mac / ".claude")
    _show(f"[②·Mac 会话目录落盘] {len(pulled)} 个文件: rules/lybra-advisor.md + skills/ {len(pulled) - 1} 个")
    assert "rules/lybra-advisor.md" in pulled and not any(k.endswith(".tmp") for k in pulled)
    assert {f".claude/{k}" for k in pulled if k.startswith("skills/")} == set(_declared_skill_members())

    # 对照: 同一实例改登记为本机 claude-code 会话目录(主机变 = 授权外 → Owner 批准)后本机 sync 落盘
    local_dir = r.tmp / "local-session"
    local_dir.mkdir()
    redeclare = (f"lybra roles redeclare --workspace-root {gov} --instance {inst} --harness claude-code --host {shlex.quote(LOCAL)} "
                 f"--dir {local_dir} --actor {inst}")
    pre = r.sh(redeclare)
    _show(f"---- ③·他机 → 本机 claude-code 改登记预演原文 ----\n{pre}")
    if "第一档" in pre:
        r.sh(redeclare + " --confirm", ok=(3,))  # 请求进收件箱(声明退出码: 未生效)
        inbox = json.loads(r.sh(f"lybra owner inbox --home-root {r.hroot} --project lybra-probe --json"))
        item = next(i for i in inbox["items"] if i["category"] == "enrollment_redeclare")
        r.sh(f"lybra owner decide --item {item['item_id']} --choice approve_redeclare --relayed-by {inst} "
             f"--owner-words '靶场: 对照本机 sync' --home-root {r.hroot} --confirm")
    r.sh(redeclare + " --confirm")
    out = r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov}")
    _show("---- ③·本机 claude-code sync 原文 ----\n" + out)
    local = _tree(local_dir / ".claude")
    # AIPOS-F255: 本机会话另有产品按本机路径渲染的会话设置与忽略文件(声明 harness_semantics.session_settings; 他机会话不适用),
    # 不属分发件, 比对分发件时扣除
    from tools.aipos_cli.session_settings import declaration as _ss_decl

    ss = _ss_decl()
    session_only = {str(Path(ss["relative_path"]).relative_to(ss["ignore"]["dir"])), ss["ignore"]["file"]}
    assert session_only <= set(local), sorted(local)
    local = {k: v for k, v in local.items() if k not in session_only}
    assert local.keys() == pulled.keys(), sorted(set(local) ^ set(pulled))
    diff = [k for k in local if local[k] != pulled[k]]
    _show(f"[②·内容比对] 本机 sync {len(local)} 个文件 vs 拉取落盘 {len(pulled)} 个文件, 字节不一致 {len(diff)} 个")
    assert diff == [], diff
    res = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))["workstations"][0]["result"]
    assert res["plan"] == [] and res["harness"]["kind"] == "claude-code"  # ③ 本机会话稳态(既有行为)


def test_item2_skills_cli_refuses_tty_and_unenrolled(tmp_path, monkeypatch):
    """`lybra skills` 拒因: stdout 为终端(tar 流不上屏)/ 未接入实例; 参数由 verbs.schema lybra_skills 生成。"""
    import pty

    gov = _rig(tmp_path, monkeypatch, launch=["pi"])
    rc, blob, err = _lybra_bytes("skills", "--role", "advisor", "--instance", f"ghost.{gov.name}.x", "--workspace-root", str(gov))
    _show(f"[②·未接入实例拒] exit {rc}: {err.strip()}")
    assert rc == 3 and blob == b"" and "未在本治理根接入" in err
    code = ("import sys\nsys.path.insert(0, %r)\nfrom tools.aipos_cli.aipos_cli import main\nsys.exit(main())\n" % str(REPO_ROOT))
    master, slave = pty.openpty()
    p = subprocess.run([sys.executable, "-c", code, "skills", "--role", "advisor", "--instance", ADV_LANTU, "--workspace-root", str(gov)],
                       stdout=slave, stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, timeout=120)
    os.close(slave)
    os.close(master)
    _show(f"[②·stdout 为终端拒] exit {p.returncode}: {p.stderr.decode().strip()}")
    assert p.returncode == 3 and "stdout 是终端" in p.stderr.decode()


def test_item3_local_shapes_unchanged():
    """③ 本机 claude-code 记录形 {kind, dir}(无 host 键)、codex 记录恒带 host 键 —— 既有形态不变; claude-code 本机目录仍须本机已存在。"""
    from tools.aipos_cli.distribution_sync import harness_role_record

    assert harness_role_record("claude-code", str(REPO_ROOT), None) == {"kind": "claude-code", "dir": str(REPO_ROOT)}
    assert harness_role_record("codex", None, None) == {"kind": "codex", "dir": None, "host": None}
    with pytest.raises(ValueError) as exc:
        harness_role_record("claude-code", "/Users/probe/not-here", None)
    assert "已存在的绝对目录" in str(exc.value) and "--harness-host" in str(exc.value)
