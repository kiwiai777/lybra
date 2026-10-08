"""AIPOS-F129 — 顾问 harness 增 codex(含跨机会话): enroll / 向导可如实登记 Codex 顾问, 凭据落治理根, 不伪装 claude-code。

件① distribution.schema harness_semantics.kinds 增 codex; harness kind 合法值 = kinds 的键(声明推导, 禁写死第二份),
    distributions[].target.harness 须 ∈ kinds(不在 = 声明错误 fail-closed); 每个 kind 的 harness_dir / harness_host 取舍与
    是否顾问会话(advisor_session)同在声明里; .lybra/role 的 harness 记 {kind: codex, dir: <可空>, host: <可空>}, 他机目录不校验本机存在。
件② `lybra roles enroll --harness codex [--harness-dir] [--harness-host]`; 未知 harness 拒并列出合法值(读声明), 零写盘。
件③ `lybra onboarding guide --advisor-harness {claude-code,codex} --advisor-host`: 取值读声明(advisor_session), 缺省 claude-code
    = 既有输出; codex 时顾问自接入写 --harness codex、不生成 .claude/skills 分发步骤; 顾问实例名 host 段 = --advisor-host >
    --host-segment, codex 两者都缺 = 拒(不默认成治理根所在机)。

test_range_* = 隔离靶场(临时 HOME / 临时 home 根 / 自起临时门, 构件复用 F92 靶场 Range / probe_range): 按 codex 向导原样走到
第 5 步, 断言治理根 .lybra/ 与 role 原文、未建 .pi 接线、未分发 .claude/skills。token 永不上屏; 注册码只在进程内传递。
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f85_onboarding_guide_generic import (  # noqa: E402  — guide 命令解析不变量的唯一实现
    SHELL_VERBS,
    _iter_command_lines,
    _parse_lybra,
    _segments,
)
from test_aipos_f92_onboarding_walkthrough import CODE_RE, Range, _cli, iso_home, probe_range  # noqa: E402,F401  — 靶场构件唯一来源

from tools.aipos_cli.onboarding import format_guide_text, generate_onboarding_guide  # noqa: E402
from tools.schema_loader import load_schema  # noqa: E402


# ===========================================================================
# 件① 声明: codex kind + 合法值单源推导
# ===========================================================================

def test_item1_kinds_are_single_derivation_source():
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds, declared_harness_kinds, harness_kind_declaration

    sem = load_schema("distribution")["harness_semantics"]
    kinds = declared_harness_kinds()
    print("[①·合法 harness kind(读 harness_semantics.kinds)]", kinds)
    assert kinds == tuple(sorted(sem["kinds"])) and "codex" in kinds and sem["default"] in kinds
    targets = {str((d.get("target") or {}).get("harness") or sem["default"]) for d in load_schema("distribution")["distributions"]}
    assert targets <= set(kinds) and "codex" in targets  # AIPOS-F136 件②: 扩展位落地——advisor-charter-codex(顾问章程)给 codex
    print("[①·顾问会话 harness(advisor_session)]", advisor_harness_kinds())
    assert advisor_harness_kinds() == ("claude-code", "codex")
    codex = harness_kind_declaration("codex")
    assert (codex["harness_dir"], codex["harness_host"], codex["advisor_session"]) == ("optional", "optional", True)
    assert (harness_kind_declaration("claude-code")["harness_dir"], harness_kind_declaration("claude-code")["harness_host"]) == ("required", "forbidden")
    assert "harness_semantics.kinds 的键" in sem["rule"]


def test_item1_undeclared_target_harness_fails_closed(monkeypatch: pytest.MonkeyPatch):
    """分发条目 target.harness 不在 kinds = 声明错误(fail-closed), 不再把它悄悄并成合法值。"""
    import tools.schema_loader as schema_loader
    from tools.aipos_cli.distribution_sync import declared_harness_kinds
    from tools.schema_loader import SchemaLoadError

    real = schema_loader.load_schema

    def fake(name, *a, **kw):
        data = real(name, *a, **kw)
        if name != "distribution":
            return data
        data = json.loads(json.dumps(data))
        data["distributions"].append({"distribution_id": "ghost", "target": {"harness": "ghost-harness", "relative_path": "x"}})
        return data

    monkeypatch.setattr(schema_loader, "load_schema", fake)
    with pytest.raises(SchemaLoadError) as exc:
        declared_harness_kinds()
    print("[①·声明不一致拒]", exc.value)
    assert "ghost-harness" in str(exc.value) and "kinds" in str(exc.value)


def test_item1_role_record_per_declaration(tmp_path: Path):
    from tools.aipos_cli.distribution_sync import harness_role_record

    local = tmp_path / "codex-session"
    local.mkdir()
    assert harness_role_record("pi", None, None) is None
    assert harness_role_record("codex", None, None) == {"kind": "codex", "dir": None, "host": None}
    assert harness_role_record("codex", None, "mac-probe") == {"kind": "codex", "dir": None, "host": "mac-probe"}
    # 他机目录: 如实记, 不校验本机存在
    assert harness_role_record("codex", "/Users/probe/hbj advisor", "mac-probe") == {
        "kind": "codex", "dir": "/Users/probe/hbj advisor", "host": "mac-probe"}
    assert not Path("/Users/probe/hbj advisor").exists()
    # 本机目录(无 host): 须已存在的绝对目录
    assert harness_role_record("codex", str(local), None) == {"kind": "codex", "dir": str(local.resolve()), "host": None}
    assert harness_role_record("claude-code", str(local), None) == {"kind": "claude-code", "dir": str(local.resolve())}
    bad = [
        ("codex", "rel/dir", "mac-probe", "绝对路径"),
        ("codex", str(tmp_path / "nope"), None, "已存在的绝对目录"),
        ("codex", None, "mac probe", "空白"),
        ("claude-code", str(local), "mac-probe", "--harness-host"),
        ("claude-code", None, None, "须给 --harness-dir"),
        ("pi", str(local), None, "--harness-dir"),
        ("pi", None, "mac-probe", "--harness-host"),
        ("gemini", None, None, "合法值"),
    ]
    for kind, d, h, needle in bad:
        with pytest.raises(ValueError) as exc:
            harness_role_record(kind, d, h)
        print(f"[①·拒 {kind} dir={d} host={h}]", exc.value)
        assert needle in str(exc.value)


def _role_ws(root: Path, harness: dict) -> Path:
    (root / ".lybra").mkdir(parents=True)
    (root / ".lybra" / "role").write_text(json.dumps({"role": "advisor", "instance": "advisor.f129probe.macprobe",
                                                      "harness": harness}), encoding="utf-8")
    return root


def test_item1_workstation_harness_reads_codex_record(tmp_path: Path):
    from tools.aipos_cli.distribution_sync import workstation_harness

    remote = workstation_harness(_role_ws(tmp_path / "g1", {"kind": "codex", "dir": "/Users/probe/x", "host": "mac-probe"}))
    print("[①·工位 harness(他机)]", remote)
    assert remote["kind"] == "codex" and remote["host"] == "mac-probe" and str(remote["dir"]) == "/Users/probe/x"
    assert remote["local"] is False
    bare = workstation_harness(_role_ws(tmp_path / "g2", {"kind": "codex", "dir": None, "host": None}))
    assert bare["dir"] is None and bare["local"] is False
    with pytest.raises(ValueError):
        workstation_harness(_role_ws(tmp_path / "g3", {"kind": "codex", "dir": "rel", "host": "mac-probe"}))
    with pytest.raises(ValueError):
        workstation_harness(_role_ws(tmp_path / "g4", {"kind": "claude-code", "dir": "/x", "host": "mac-probe"}))


# ===========================================================================
# 件② enroll --harness: 未知值拒并列出合法值(读声明), 零写盘
# ===========================================================================

def test_item2_unknown_harness_refused_lists_declared(tmp_path: Path, iso_home: Path):
    from tools.aipos_cli.distribution_sync import declared_harness_kinds

    ws = tmp_path / "gov"
    rc, out = _cli("roles", "enroll", "--code", "LYBRAENROLL1.fixture-not-a-code", "--gate-url", "http://127.0.0.1:9",
                   "--workspace", str(ws), "--harness", "gemini")
    print("[②·未知 harness 原文]\n" + out)
    assert rc != 0 and "'gemini'" in out and str(list(declared_harness_kinds())) in out
    assert not ws.exists()  # 先于兑换 / 写盘拒
    rc, out = _cli("roles", "enroll", "--code", "LYBRAENROLL1.fixture-not-a-code", "--gate-url", "http://127.0.0.1:9",
                   "--workspace", str(ws), "--harness", "claude-code", "--harness-dir", str(tmp_path), "--harness-host", "mac-probe")
    print("[②·claude-code 带 --harness-host 原文]\n" + out)
    assert rc != 0 and "--harness-host" in out and not ws.exists()


def test_item2_enroll_parser_has_harness_host():
    from tools.aipos_cli.aipos_cli import build_parser

    args = build_parser().parse_args(["roles", "enroll", "--code", "X", "--harness", "codex", "--harness-host", "mac-probe",
                                      "--harness-dir", "/Users/probe/x"])
    assert (args.harness, args.harness_host, args.harness_dir) == ("codex", "mac-probe", "/Users/probe/x")


# ===========================================================================
# 件③ onboarding guide --advisor-harness / --advisor-host
# ===========================================================================

BASE = dict(project_name="f129-probe", home_root="/tmp/f129-home", gate_url="http://127.0.0.1:7118", host_segment="devbox",
            workspace_dir="/tmp/f129-ws/exec", auditor_dir="/tmp/f129-ws/audit", owner_workspace="/tmp/f129-home/ops",
            code_repo="/tmp/f129-code/app")


def _lybra_args(step: dict) -> list:
    return [_parse_lybra(seg[1:], "step") for _, l in _iter_command_lines({"steps": [step]}) for seg in _segments(l) if seg[0] == "lybra"]


def test_item3_guide_codex_vs_claude_code_default():
    cc = generate_onboarding_guide(**BASE, advisor_dir="/tmp/f129-session")
    cc_explicit = generate_onboarding_guide(**BASE, advisor_dir="/tmp/f129-session", advisor_harness="claude-code")
    cx = generate_onboarding_guide(**BASE, advisor_harness="codex", advisor_host="mac-probe.local")
    cc5, cx5 = cc["steps"][4], cx["steps"][4]
    print("[③·claude-code(缺省)第 5 步]\n" + cc5["command"])
    print("[③·codex 第 5 步]\n" + cx5["command"])
    assert cc5 == cc_explicit["steps"][4]  # 缺省 = 显式 claude-code(既有行为)
    a_cc = _lybra_args(cc5)
    assert (a_cc[0].harness, a_cc[0].harness_dir, a_cc[0].harness_host) == ("claude-code", "/tmp/f129-session", None)
    assert a_cc[1].command == "sync" and a_cc[1].dry_run is True and ".claude/skills" in cc5["creates"]
    a_cx = _lybra_args(cx5)
    assert len(a_cx) == 1  # 无 sync 分发步骤(会话在他机: 本机无落点, AIPOS-F136 起声明给 codex 的章程列 undelivered)
    assert (a_cx[0].roles_command, a_cx[0].workspace, a_cx[0].harness, a_cx[0].harness_host, a_cx[0].harness_dir) == (
        "enroll", cx["governance_root"], "codex", "mac-probe.local", None)
    blob = json.dumps(cx5, ensure_ascii=False)
    assert ".claude" not in blob and "claude-code" not in blob
    # 实例名: 顾问 host 段 = --advisor-host 短名; 工位仍按 --host-segment
    assert cx["instances"] == {"advisor": "advisor.f129-probe.mac-probe", "executor": "exec.f129-probe.devbox",
                               "auditor": "audit.f129-probe.devbox"}
    assert cc["instances"]["advisor"] == "advisor.f129-probe.devbox"
    assert cx["advisor_harness"] == {"kind": "codex", "host": "mac-probe.local", "dir": None}
    # 其余各步与 claude-code 一致(除顾问实例名替换)
    for n in (1, 2, 6, 7, 8, 9):
        assert cx["steps"][n - 1]["command"].replace("mac-probe", "devbox") == cc["steps"][n - 1]["command"], n
    # 带 --advisor-dir 时如实写 --harness-dir(他机路径不校验)
    cxd = generate_onboarding_guide(**BASE, advisor_harness="codex", advisor_host="mac-probe", advisor_dir="/Users/probe/hbj advisor")
    assert _lybra_args(cxd["steps"][4])[0].harness_dir == "/Users/probe/hbj advisor"


def test_item3_guide_codex_every_command_parses():
    for kw in (dict(BASE, advisor_harness="codex", advisor_host="mac-probe"),
               dict(project_name="chris-huibojin", home_root="/tmp/f129 home", advisor_harness="codex", advisor_host="mac-probe",
                    advisor_dir="~/chris session", repos=["app=/tmp/f129 code/app", "lib=/tmp/f129 code/lib"], default_repo="app")):
        guide = generate_onboarding_guide(**kw)
        n = 0
        for _, line in _iter_command_lines(guide):
            if line.startswith("/"):
                continue
            for seg in _segments(line):
                if seg[0] == "lybra":
                    _parse_lybra(seg[1:], line)
                    n += 1
                else:
                    assert seg[0] in SHELL_VERBS, line
        print(f"[③·codex guide {kw['project_name']}] lybra 命令解析 {n} 条")
        assert n >= 10 and "--harness codex" in format_guide_text(guide)


def test_item3_guide_refusals_read_declaration():
    with pytest.raises(ValueError) as exc:
        generate_onboarding_guide(**{**BASE, "host_segment": None}, advisor_harness="codex")
    print("[③·codex 缺 host 拒]", exc.value)
    assert "--advisor-host" in str(exc.value)
    # --host-segment 显式给出时 codex 顾问实例沿用它(不默认成治理根所在机, 也不拒)
    assert generate_onboarding_guide(**BASE, advisor_harness="codex")["instances"]["advisor"] == "advisor.f129-probe.devbox"
    with pytest.raises(ValueError) as exc:
        generate_onboarding_guide(**BASE, advisor_harness="claude-code", advisor_host="mac-probe")
    print("[③·claude-code 带 --advisor-host 拒]", exc.value)
    assert "claude-code" in str(exc.value)
    with pytest.raises(ValueError) as exc:
        generate_onboarding_guide(**BASE, advisor_harness="pi")
    print("[③·pi 非顾问会话 harness 拒]", exc.value)
    assert "codex" in str(exc.value)


def test_item3_cli_advisor_harness_choices_from_declaration(iso_home: Path):
    from tools.aipos_cli.distribution_sync import advisor_harness_kinds

    rc, out = _cli("onboarding", "guide", "f129-probe", "--home-root", str(iso_home / "h"), "--advisor-harness", "pi")
    print("[③·CLI --advisor-harness pi]\n" + out)
    assert rc == 2 and all(k in out for k in advisor_harness_kinds())
    rc, out = _cli("onboarding", "guide", "f129-probe", "--home-root", str(iso_home / "h"), "--gate-url", "http://127.0.0.1:7118",
                   "--advisor-harness", "codex", "--advisor-host", "mac-probe", "--json")
    assert rc == 0, out
    guide = json.loads(out)
    assert guide["advisor_harness"]["kind"] == "codex" and "--harness codex" in guide["steps"][4]["command"]
    rc, out = _cli("onboarding", "guide", "f129-probe", "--home-root", str(iso_home / "h"), "--gate-url", "http://127.0.0.1:7118",
                   "--advisor-harness", "codex")
    print("[③·CLI codex 缺 host]\n" + out)
    assert rc != 0 and "--advisor-host" in out


# ===========================================================================
# 隔离靶场: 按 codex 向导走到第 5 步(真门进程 lybra serve, 临时端口)
# ===========================================================================

def test_range_codex_advisor_enroll_lands_governance_root(probe_range: Range):
    r = probe_range
    gov = r.hroot / "lybra-probe"
    guide_args = (f"lybra-probe --repo app={r.session}/app --default-repo app --advisor-harness codex --advisor-host mac-probe "
                  f"--host-segment devbox --envelope-days 7 --max-tasks 20")
    guide = json.loads(r.sh(f"lybra onboarding guide {guide_args} --json"))
    assert guide["instances"]["advisor"] == "advisor.lybra-probe.mac-probe"
    fills: dict[str, str] = {}
    step3 = ""
    for step in guide["steps"][:5]:
        for line in (l.strip() for l in step["command"].splitlines()):
            if not line or line.startswith("#"):
                continue
            for k, v in fills.items():
                line = line.replace(k, v)
            out = r.sh(line)
            if "enroll-code" in line:
                step3 = line
                fills["<ADVISOR_CODE>"] = CODE_RE.findall(out)[0]
            if "roles enroll " in line:
                print("[靶场·第 5 步 enroll 输出]\n" + CODE_RE.sub("LYBRAENROLL1.<redacted>", out))
                assert "✓ Enrollment successful" in out and "Instance: advisor.lybra-probe.mac-probe" in out
    role_text = (gov / ".lybra" / "role").read_text(encoding="utf-8")
    print("[靶场·治理根 .lybra/role 原文]\n" + role_text)
    role = json.loads(role_text)
    assert role["harness"] == {"kind": "codex", "dir": None, "host": "mac-probe"}
    assert role["instance"] == "advisor.lybra-probe.mac-probe" and role["owner_policy_ref"] == guide["policies"]["driver"]
    assert (gov / ".lybra" / "connection.json").is_file()
    assert not (gov / ".pi").exists() and not (gov / "AGENTS.md").exists()  # 未建 .pi 接线 / 未落章程
    claude = sorted(str(p) for base in (r.home, r.tmp) for p in base.rglob(".claude"))
    print("[靶场·.claude 目录]", claude or "无")
    assert claude == []
    sync = json.loads(r.sh(f"lybra sync --harness-root {gov} --workspace-root {gov} --dry-run --json"))
    ws0 = sync["workstations"][0]
    print("[靶场·sync --dry-run]", json.dumps({k: ws0[k] for k in ("status",)}, ensure_ascii=False), ws0["result"].get("note"))
    assert ws0["status"] == "dry-run" and ws0["result"]["plan"] == [] and ws0["result"]["harness"]["kind"] == "codex"

    # 他机会话目录: 如实记 host 与路径, 不校验本机存在(第二个码, 同一 Owner 签发命令)
    code2 = CODE_RE.findall(r.sh(step3))[0]
    remote_dir = "/Users/probe/hbj advisor"
    out = r.sh(f"lybra roles enroll --code {code2} --workspace {shlex.quote(str(gov))} --harness codex "
               f"--harness-dir {shlex.quote(remote_dir)} --harness-host mac-probe --verify")
    assert "✓ Enrollment successful" in out
    role_text = (gov / ".lybra" / "role").read_text(encoding="utf-8")
    print("[靶场·他机目录 .lybra/role 原文]\n" + role_text)
    assert json.loads(role_text)["harness"] == {"kind": "codex", "dir": remote_dir, "host": "mac-probe"}
    assert not Path(remote_dir).exists() and not (gov / ".pi").exists()
    blob = "\n".join(r.log)
    for tok in json.loads((gov / ".lybra" / "connection.json").read_text(encoding="utf-8"))["tokens"]:
        assert tok["token"] not in blob  # token 永不上屏
