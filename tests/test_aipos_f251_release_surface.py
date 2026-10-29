"""AIPOS-F251 — 受控生产发布面(发布目标声明 + 只新建写边界 + production 禁区授权 + plan / apply / verify / rollback)。

靶场(禁真门 / 真治理根 / 真工位 / 真 pi / 任何真实生产主机):
  - 临时治理根(项目 webdemo, 卡号形 WEB-<n>: 不写死 lybra / AIPOS)+ 临时源仓 web(固定提交 = 门生 PASS 裁决覆盖的 WEB-2 合入)+ 临时治理仓远端;
  - 模拟「生产主机」sim-prod = 本机临时目录 <tmp>/prod(opt/kiwiai/web 与 var/www/html 里预置存量条目, 全程核其不变);
    传输夹具 = PATH 最前的假 ssh(记录别名与远端命令, 把远端命令就地交 sh, stdin 原样透传 = helper 引导 + 请求);
  - 本机主机另起一条发布目标(本机直接执行 + 本机 lstat 判已存在);
  - 项目授权修订由 record_owner_decision 预演产出的修订原文落盘(人肉期样本在临时根自造; 签发经门的链路由 F224 夹具覆盖)。
验收原文经 _show 打到 stdout(RETURN 证据)。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_aipos_f78_engine_agnostic as f78  # noqa: E402  — 靶场骨架唯一来源
from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402

PROJECT = "webdemo"
EXEC = f"exec.{PROJECT}.devbox"
AUD = f"audit.{PROJECT}.devbox"
ADVISOR = f"advisor.{PROJECT}.devbox"
CARD, SRC_CARD, AUDIT_CARD = "WEB-3", "WEB-2", "WEB-3R"
HOST, ALIAS = "sim-prod", "sim-prod-alias"
TARGET, LOCAL_TARGET, BAD_TARGET = "site", "site-local", "site-badsrc"
AUTH = f"auth_{PROJECT}"
OWNER_REF = "Owner 10-30 定: 官网首次上线(靶场)"
SITE_FILES = {"index.html": "<h1>home</h1>\n", "fde.html": "<h1>fde</h1>\n", "README.md": "# not public\n",
              "assets/app.css": "body{}\n", "assets/img/logo.svg": "<svg/>\n"}


def _show(text: str) -> None:
    sys.__stdout__.write(text + "\n")
    sys.__stdout__.flush()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    text = "".join(ln for ln in out.getvalue().splitlines(keepends=True) if not ln.startswith("治理根: "))
    return int(rc or 0), text, err.getvalue()


def _rel(r: dict, *argv: str) -> tuple[int, str, str]:
    return _cli("--workspace-root", r["gov"], "release", *argv)


def _rt(r: dict, action: str, *argv: str) -> tuple[int, str, str]:
    return _cli("project", "release-target", action, "--workspace-root", r["gov"], *argv)


def _site_repo(path: Path) -> tuple[str, str]:
    f78._init_product_repo(path)
    for rel, text in SITE_FILES.items():
        f78._write(path / rel, text)
    (path / "alias.html").symlink_to("index.html")  # 源码符号链接(白名单含它的目标 = plan 拒)
    f78._git(path, "add", "-A")
    f78._git(path, "commit", "-q", "-m", f"Merge card/{SRC_CARD}: 官网五页")
    audited = f78._git(path, "rev-parse", "HEAD")
    f78._write(path / "index.html", "<h1>unaudited</h1>\n")
    f78._git(path, "commit", "-qam", "WEB-9: 未审改动")
    unaudited = f78._git(path, "rev-parse", "HEAD")
    return audited, unaudited


def _verdict(gov: Path, task_id: str, commit: str) -> str:
    vid = f"verdict_{task_id}_20261029_010000_audit-webdemo-devbox"
    f78._write(gov / "5_tasks" / "records" / "audit_verdicts" / task_id / f"{vid}.md",
               "---\nrecord_type: audit_verdict_record\nevent_type: mcp_audit_verdict\n"
               f"verdict_id: {vid}\nverdict_at: '2026-10-29T01:00:00Z'\nreviewed_task_id: {task_id}\n"
               f"verdict: PASS\nauditor_instance: {AUD}\nartifact_subject:\n  commit_sha: {commit}\n  tree_hash: dummy\n---\n# verdict\n")
    return vid


def _claim(gov: Path, task_id: str, policy_ref: str) -> None:
    for old in (gov / "5_tasks" / "records" / "claims" / task_id).glob("*.md") if (gov / "5_tasks" / "records" / "claims" / task_id).is_dir() else []:
        old.unlink()
    f78._write(gov / "5_tasks" / "records" / "claims" / task_id / f"claim_{task_id}_20261030_000000_{EXEC}.md",
               f78._fm({"record_type": "claim_record", "event_type": "mcp_queue_claim", "claim_id": f"claim_{task_id}_x", "task_id": task_id,
                        "actor": EXEC, "agent_instance": EXEC, "owner_policy_ref": policy_ref, "claimed_at": "'2026-10-30T00:00:00Z'"}))


def _fake_ssh(tmp: Path, mp: pytest.MonkeyPatch) -> Path:
    """传输夹具: 假 ssh(丢弃别名、记录、把远端命令交本机 sh, stdin 原样透传)。"""
    bindir = tmp / "fakebin"
    bindir.mkdir()
    log = tmp / "fake-ssh.log"
    script = bindir / "ssh"
    script.write_text('#!/bin/sh\n[ "$1" = "--" ] && shift\nalias="$1"; shift\nprintf \'%s\\t%.60s\\n\' "$alias" "$*" >> "$FAKE_SSH_LOG"\n'
                      'exec sh -c "$*"\n', encoding="utf-8")
    script.chmod(0o755)
    mp.setenv("FAKE_SSH_LOG", str(log))
    mp.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


def _tree_state(root: Path) -> dict[str, str]:
    """生产存量快照(lstat, 不跟随链接): 相对路径 → 类型 / 链接目标 / 内容。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            rel = str(p.relative_to(root))
            out[rel] = f"link:{os.readlink(p)}" if p.is_symlink() else ("dir" if p.is_dir() else f"file:{p.read_bytes()!r}")
    return out


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    from tools.aipos_cli import validation_root as vr

    gov = f78._make_gov(tmp_path, monkeypatch, shape="lybra", project=PROJECT)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    f78._write(gov / "card_policy.json", json.dumps({"schema_version": "1.0.0", "task_id_pattern": "WEB-[0-9]+"}))
    web = tmp_path / "repos" / "web"
    audited, unaudited = _site_repo(web)
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    data["repos"] = {"default": "main", "items": {"main": data["code_repo"], "web": str(web)}}
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    vid = _verdict(gov, SRC_CARD, audited)
    f78._card(gov, CARD, "claimed", assigned=EXEC,
              extra={"project": PROJECT, "authorization_zones": ["production"], "lane": {"repo": "web", "paths": ["docs/"]}})
    f78._card(gov, AUDIT_CARD, "pending", assigned=AUD,
              extra={"project": PROJECT, "task_mode": "audit", "reviewed_task_id": CARD, "lane": {"repo": "web", "paths": ["docs/"]}})
    _claim(gov, CARD, AUTH)
    ssh_log = _fake_ssh(tmp_path, monkeypatch)
    prod = tmp_path / "prod"
    (prod / "opt" / "kiwiai" / "web" / "clients").mkdir(parents=True)
    f78._write(prod / "opt" / "kiwiai" / "web" / "clients" / "a.html", "client a\n")
    html = prod / "var" / "www" / "html"
    html.mkdir(parents=True)
    f78._write(html / "qr.html", "qr\n")
    (html / "kiwiai").symlink_to(prod / "opt" / "kiwiai" / "web" / "clients")
    local = tmp_path / "localprod"
    (local / "srv").mkdir(parents=True)
    (local / "www").mkdir(parents=True)
    init_governance_repo(gov)
    return {"gov": gov, "web": web, "audited": audited, "unaudited": unaudited, "tree": f78._git(web, "rev-parse", f"{audited}^{{tree}}"),
            "verdict": vid, "prod": prod, "html": html, "root": prod / "opt" / "kiwiai" / "web" / "website", "local": local,
            "ssh_log": ssh_log, "local_label": vr.local_host_label(), "tmp": tmp_path}


def _entries(r: dict) -> list[str]:
    h = r["html"]
    return ["--entry", f"{h}/index.html=current/index.html", "--entry", f"{h}/fde.html=current/fde.html",
            "--entry", f"{h}/assets=current/assets"]


def _declare_remote(r: dict, *extra: str, confirm: bool = True) -> tuple[int, str, str]:
    rc, out, err = _cli("project", "validation-root", "set-host", "--workspace-root", r["gov"], "--host", HOST, "--ssh-alias", ALIAS,
                        "--owner-ref", OWNER_REF, "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    argv = ["--id", TARGET, "--host", HOST, "--repo", "web", "--allow", "index.html", "--allow", "fde.html", "--allow", "assets/",
            "--release-root", str(r["root"]), *_entries(r), "--owner-ref", OWNER_REF, "--actor", ADVISOR, *extra]
    return _rt(r, "declare", *argv, *(["--confirm"] if confirm else []))


def _auth_revision(r: dict, *, action: str, hosts: list[str], zones: list[str]) -> str:
    """项目授权修订(预演原文落盘; 只追加修订)。"""
    from tools.aipos_cli import autonomy_policy as ap
    from tools.aipos_cli.board_adapter import record_owner_decision

    gov = r["gov"]
    block = ap.authorization_block(gov, action=action, lanes=[{"repo": "web", "task_modes": ["code"], "drivers": [ADVISOR]}],
                                   hosts=hosts, zones_open=zones, max_tasks=10, expires_at="2999-01-01T00:00:00Z")
    payload = {"decision_id": f"grant-{block['authorization_id']}-r{int(block['base_revision']) + 1:04d}", "project_authorization": block,
               "actor": "owner", "decided_by_ref": "owner"}
    preview = record_owner_decision(payload, dry_run=True, repo_root=gov, actor="owner")
    data = preview["data"]
    assert not preview.get("blocking_reasons"), preview
    f78._write(gov / data["authorization_path"], data["authorization_markdown"])
    return str(data["authorization_path"])


def _plan(r: dict, *extra: str, target: str = TARGET, commit: str | None = None) -> tuple[int, str, str]:
    return _rel(r, "plan", "--task-id", CARD, "--target", target, "--commit", commit or r["audited"], "--actor", EXEC, *extra)


def _plan_hash(out: str) -> str:
    return next(ln.split("plan_hash=")[1].strip() for ln in out.splitlines() if ln.startswith("PLAN_OK"))


# ===========================================================================
# 件① 声明: 两阶段 / 落账 / list / 不合拒
# ===========================================================================

def test_item1_declare_two_phase_landing_and_refusals(rig):
    r = rig
    rc, out, err = _declare_remote(r, confirm=False)
    _show(f"---- 件① declare 预演 exit {rc} ----\n{out}{err}")
    assert rc == 0 and "预览" in out and "release_targets" in out
    assert "release_targets" not in json.loads((r["gov"] / "project.json").read_text())
    rc, out, err = _declare_remote(r)
    _show(f"---- 件① declare --confirm exit {rc} ----\n{out}{err}")
    assert rc == 0 and "已写入" in out and "落账" in out + err
    decl = json.loads((r["gov"] / "project.json").read_text())["release_targets"][TARGET]
    assert decl["entry_order"] == [f"{r['html']}/fde.html", f"{r['html']}/assets", f"{r['html']}/index.html"]  # 缺省首页最后
    log = f78._git(r["gov"], "log", "-1", "--format=%s")
    assert "release-target declare" in log, log
    rc, out, _err = _rt(r, "list")
    _show(f"---- 件① list ----\n{out}")
    assert rc == 0 and f"{TARGET}: 有效 主机 {HOST}(ssh {ALIAS})" in out
    bad = {
        "未声明 ssh 别名的主机": (["--host", "other-host"], "RT_HOST_UNDECLARED"),
        "白名单 ..": (["--allow", "../secret"], "RT_DECLARATION_INVALID"),
        "发布根 = 禁区目录本身": (["--release-root", "/opt"], "RT_DECLARATION_INVALID"),
        "入口对象不在白名单": (["--entry", f"{r['html']}/README.md=current/README.md"], "RT_DECLARATION_INVALID"),
        "入口在发布根内": (["--entry", f"{r['root']}2/x.html=current/index.html"], "RT_DECLARATION_INVALID"),
    }
    for what, (patch, code) in bad.items():
        argv = ["--id", "probe", "--host", HOST, "--repo", "web", "--allow", "index.html", "--release-root", str(r["root"]) + "2",
                "--entry", f"{r['html']}/x.html=current/index.html", "--owner-ref", OWNER_REF, "--actor", ADVISOR]
        for i in range(0, len(patch), 2):
            flag, value = patch[i], patch[i + 1]
            if flag in ("--allow", "--entry"):
                argv += [flag, value]
            else:
                argv[argv.index(flag) + 1] = value
        rc, out, err = _rt(r, "declare", *argv, "--confirm")
        _show(f"[件① 拒·{what}] exit {rc}: {err.strip().splitlines()[0]} | {(err.strip().splitlines() + [''])[1].strip()[:160]}")
        assert rc == 1 and code in err, (what, err)
    rc, out, err = _rt(r, "declare", "--id", "noref", "--host", HOST, "--repo", "web", "--allow", "index.html", "--release-root",
                       str(r["root"]) + "3", "--entry", f"{r['html']}/y.html=current/index.html", "--actor", ADVISOR, "--confirm")
    _show(f"[件① 拒·无项目授权且缺 --owner-ref] exit {rc}: {err.strip()}")
    assert rc == 1 and "--owner-ref" in err
    assert sorted(json.loads((r["gov"] / "project.json").read_text())["release_targets"]) == [TARGET]


# ===========================================================================
# 件② 写边界: 精确路径允许 / 父目录其他条目拒 / 已存在拒 / 只给 append / 审计只读 / validation_root 面不受影响
# ===========================================================================

def test_item2_write_boundary_check(rig):
    from tools.aipos_cli.write_boundary import build_write_boundary, check_access

    r = rig
    assert _declare_remote(r)[0] == 0
    root, html, sha = r["root"], r["html"], r["audited"]
    # 本机发布目标(本机 lstat 判已存在)
    rc, out, err = _rt(r, "declare", "--id", LOCAL_TARGET, "--host", r["local_label"], "--repo", "web", "--allow", "index.html",
                       "--release-root", str(r["local"] / "srv" / "site"), "--entry", f"{r['local'] / 'www'}/index.html=current/index.html",
                       "--owner-ref", OWNER_REF, "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    # validation_root 声明(同一主机; 夹具直写 project.json 段, 判定只读声明)
    data = json.loads((r["gov"] / "project.json").read_text())
    vbase = r["prod"] / "home" / "kiwi" / "lybra-validation"
    data["validation_roots"]["declarations"] = [{
        "id": "vr-web-3-executor-1", "host": HOST, "host_fingerprint": "hfp1:" + "0" * 32, "base": str(vbase), "task_id": CARD,
        "instance": EXEC, "role_class": "executor", "owner_ref": OWNER_REF, "declared_by": ADVISOR, "declared_at": "2026-10-30T00:00:00Z",
        "expires_at": "2999-01-01T00:00:00Z", "revoked_at": None, "revoked_by": None, "revoke_reason": None,
        "runs": [{"run_id": "run-1", "candidate": sha, "started_at": "2026-10-30T00:00:00Z", "started_by": ADVISOR, "finished_at": None,
                  "finished_by": None}]}]
    (r["gov"] / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    cases = [
        ("executor", f"{root}", "append", HOST, True, "OK"),
        ("executor", f"{root}/releases", "append", HOST, True, "OK"),
        ("executor", f"{root}/releases/{sha}/assets/app.css", "append", HOST, True, "OK"),
        ("executor", f"{root}/current", "append", HOST, True, "OK"),
        ("executor", f"{html}/index.html", "append", HOST, True, "OK"),
        ("executor", f"{html}/qr.html", "append", HOST, False, "RELEASE_PATH_NOT_DECLARED"),
        ("executor", f"{html}", "append", HOST, False, "RELEASE_PATH_NOT_DECLARED"),
        ("executor", f"{root.parent}/clients/a.html", "append", HOST, False, "RELEASE_PATH_NOT_DECLARED"),
        ("executor", f"{root}/other", "append", HOST, False, "RELEASE_PATH_NOT_DECLARED"),
        ("executor", f"{root}/releases/notasha/x", "append", HOST, False, "RELEASE_PATH_NOT_DECLARED"),
        ("executor", f"{root}/releases/{sha}/../../x", "append", HOST, False, "PATH_NOT_CANONICAL"),
        ("executor", f"{html}/index.html", "mutate", HOST, False, "LEVEL_EXCEEDED"),
        ("auditor", f"{html}/index.html", "append", HOST, False, "LEVEL_EXCEEDED"),
        ("auditor", f"{html}/index.html", "read", HOST, True, "OK"),
        ("advisor", f"{root}/current", "append", HOST, False, "LEVEL_EXCEEDED"),
        ("executor", "/somewhere/else", "append", HOST, False, "SURFACE_UNDECLARED"),
    ]
    for role, path, level, host, allowed, code in cases:
        got = check_access(r["gov"], role=role, path=path, level=level, host=host)
        _show(f"[件② {role} {level} {path.replace(str(r['tmp']), '<tmp>')}] {'ALLOW' if got['allowed'] else 'DENY'} [{got['code']}] "
              f"面={got.get('surface')}")
        assert (got["allowed"], got["code"]) == (allowed, code), (role, path, got)
        if got.get("surface") == "release_target" or code.startswith("RELEASE"):
            assert got["surface"] == "release_target" and "验证根" not in got["reason"]
    # validation_root 面不受影响: 同主机验证根 run 目录照旧走 validation_root 面判定
    vr_ok = check_access(r["gov"], role="executor", path=f"{vbase}/{CARD}/run-1/out.txt", level="mutate", host=HOST, instance=EXEC,
                         task_id=CARD, run_id="run-1")
    vr_cross = check_access(r["gov"], role="executor", path=f"{vbase}/{CARD}/run-9/out.txt", level="mutate", host=HOST, instance=EXEC,
                            task_id=CARD, run_id="run-1")
    _show(f"[件② validation_root 不受影响] 本 run: {vr_ok['allowed']} [{vr_ok['code']}] 面={vr_ok['surface']}; 跨 run: "
          f"{vr_cross['allowed']} [{vr_cross['code']}] 面={vr_cross['surface']}")
    assert vr_ok["allowed"] and vr_ok["surface"] == "validation_root" and vr_cross["code"] == "NOT_OWN_RUN"
    # 本机: 已存在(普通文件 / 悬空链接)= RELEASE_TARGET_EXISTS
    lroot, lwww = r["local"] / "srv" / "site", r["local"] / "www"
    fresh = check_access(r["gov"], role="executor", path=str(lwww / "index.html"), level="append")
    (lwww / "index.html").symlink_to(r["local"] / "nowhere")
    dangling = check_access(r["gov"], role="executor", path=str(lwww / "index.html"), level="append")
    (lwww / "index.html").unlink()
    lroot.mkdir()
    existing = check_access(r["gov"], role="executor", path=str(lroot), level="append")
    lroot.rmdir()
    _show(f"[件② 本机] 未存在 {fresh['code']}; 悬空链接 {dangling['code']}: {dangling['reason']}; 已存在目录 {existing['code']}")
    assert fresh["allowed"] and (dangling["code"], existing["code"]) == ("RELEASE_TARGET_EXISTS", "RELEASE_TARGET_EXISTS")
    # CLI 原文(KIWIWEB-003 预检同一命令形: --level append)
    rc, out, err = _cli("roles", "--workspace-root", r["gov"], "write-boundary", "--role", "executor", "--check", f"{html}/index.html",
                        "--host", HOST, "--level", "append")
    rc2, out2, _ = _cli("roles", "--workspace-root", r["gov"], "write-boundary", "--role", "executor", "--check", f"{html}/qr.html",
                        "--host", HOST, "--level", "append")
    _show(f"---- 件② CLI --check 正例 exit {rc} ----\n{out}---- 反例 exit {rc2} ----\n{out2}")
    assert rc == 0 and out.startswith("ALLOW [OK]") and rc2 == 3 and out2.startswith("DENY [RELEASE_PATH_NOT_DECLARED]")
    rc, out, err = _rt(r, "revoke", "--id", LOCAL_TARGET, "--reason", "靶场撤销", "--actor", ADVISOR, "--confirm")
    revoked = check_access(r["gov"], role="executor", path=str(lwww / "index.html"), level="append")
    rc2, _o, err2 = _rt(r, "revoke", "--id", LOCAL_TARGET, "--reason", "再撤", "--actor", ADVISOR, "--confirm")
    _show(f"[件① revoke] exit {rc}; 撤销后判定 {revoked['code']}: {revoked['reason']}; 再撤 exit {rc2}: {err2.strip().splitlines()[0]}")
    assert rc == 0 and revoked["code"] == "RELEASE_TARGET_REVOKED" and rc2 == 1 and "RT_STATE_CONFLICT" in err2
    rows = {(row["role"], row["surface"]): row["level"] for row in build_write_boundary(r["gov"])["rows"]}
    assert rows[("executor", "release_target")] == "append" and rows[("auditor", "release_target")] == "read"
    assert rows[("advisor", "release_target")] == "read"


# ===========================================================================
# 件④ plan 各拒因
# ===========================================================================

def test_item4_plan_refusals(rig):
    r = rig
    assert _declare_remote(r)[0] == 0
    html = r["html"]
    cases = []
    (html / "fde.html").write_text("someone else\n", encoding="utf-8")
    cases.append(("已存在目标", _plan(r), "RELEASE_PLAN_REFUSED", "fde.html: 目的路径已存在"))
    (html / "fde.html").unlink()
    (html / "index.html").symlink_to(html / "gone.html")
    cases.append(("悬空链接", _plan(r), "RELEASE_PLAN_REFUSED", "悬空链接"))
    (html / "index.html").unlink()
    r["root"].mkdir()
    cases.append(("发布根已存在", _plan(r), "RELEASE_PLAN_REFUSED", "website: 目的路径已存在"))
    r["root"].rmdir()
    html.chmod(0o555)
    cases.append(("入口父目录不可写(禁提权)", _plan(r), "RELEASE_PLAN_REFUSED", "父目录当前用户不可写"))
    html.chmod(0o755)
    cases.append(("白名单外", _plan(r, "--path", "README.md"), "RELEASE_SOURCE_REJECTED", "不在发布白名单"))
    cases.append(("..", _plan(r, "--path", "../index.html"), "RELEASE_SOURCE_REJECTED", "路径穿越"))
    cases.append(("入口对象未选", _plan(r, "--path", "index.html", "--path", "assets/"), "RELEASE_SOURCE_REJECTED", "不在本次发布内容内"))
    cases.append(("未审计提交", _plan(r, commit=r["unaudited"]), "RELEASE_COMMIT_NOT_AUDITED", "未被门生 PASS 裁决覆盖"))
    cases.append(("预期 tree 不符", _plan(r, "--tree", "0" * 40), "RELEASE_COMMIT_INVALID", "预期"))
    rc, out, err = _rt(r, "declare", "--id", BAD_TARGET, "--host", HOST, "--repo", "web", "--allow", "alias.html", "--release-root",
                       str(r["root"]) + "-b", "--entry", f"{html}/alias.html=current/alias.html", "--owner-ref", OWNER_REF,
                       "--actor", ADVISOR, "--confirm")
    assert rc == 0, out + err
    cases.append(("源码符号链接", _plan(r, target=BAD_TARGET), "RELEASE_SOURCE_REJECTED", "符号链接"))
    before = _tree_state(r["prod"])
    for what, (rc, out, err), code, needle in cases:
        _show(f"[件④ plan 拒·{what}] exit {rc}: {' | '.join(l.strip() for l in err.strip().splitlines()[:3])[:300]}")
        assert rc == 1 and code in err and needle in err, (what, out, err)
    assert _tree_state(r["prod"]) == before and not r["root"].exists()
    events = r["gov"] / "5_tasks" / "records" / "events" / CARD
    assert not events.exists() or not list(events.glob("release_plan_*.md"))  # 拒 = 不落计划记录


# ===========================================================================
# 件③ 授权: 无项目授权 / 旧信封 / 未放开 production / 主机不在授权内 / 卡面未声明 production 各自被拒且给出口
# ===========================================================================

def test_item3_authorization_refusals(rig):
    r = rig
    assert _declare_remote(r)[0] == 0
    rc, out, err = _plan(r)
    assert rc == 0, out + err
    digest = _plan_hash(out)
    _show(f"---- 件③ plan(授权未就绪仍可计划, 只读)----\n{out}")
    assert "授权: 未就绪 [RELEASE_NOT_AUTHORIZED]" in out

    def apply() -> tuple[int, str]:
        rc, out, err = _rel(r, "apply", "--task-id", CARD, "--target", TARGET, "--plan-hash", digest, "--actor", EXEC)
        return rc, out + err

    rc, text = apply()
    _show(f"[件③ 无项目授权] exit {rc}\n{text}")
    assert rc == 1 and "RELEASE_NOT_AUTHORIZED" in text and "--open-zone production" in text and f"--host {HOST}" in text
    _auth_revision(r, action="grant", hosts=["devbox"], zones=[])
    _claim(r["gov"], CARD, "pol_old_envelope")
    rc, text = apply()
    _show(f"[件③ 本卡只用旧信封] exit {rc}\n{text}")
    assert rc == 1 and "不是项目授权" in text and "grant sign" in text
    _claim(r["gov"], CARD, AUTH)
    rc, text = apply()
    _show(f"[件③ 未放开 production] exit {rc}\n{text}")
    assert rc == 1 and "禁区" in text and "grant extend" in text and "--open-zone production" in text
    _auth_revision(r, action="extend", hosts=[], zones=["production"])
    rc, text = apply()
    _show(f"[件③ 主机不在授权内] exit {rc}\n{text}")
    assert rc == 1 and "授权外主机" in text and f"--host {HOST}" in text and "grant extend" in text
    card = r["gov"] / "5_tasks" / "queue" / "claimed" / f"{CARD.lower()}.md"
    original = card.read_text(encoding="utf-8")
    card.write_text(original.replace('authorization_zones: ["production"]', "authorization_zones: []"), encoding="utf-8")
    _auth_revision(r, action="extend", hosts=[HOST], zones=[])
    rc, text = apply()
    _show(f"[件③ 卡面未声明 production] exit {rc}\n{text}")
    assert rc == 1 and "RELEASE_ZONE_UNDECLARED" in text
    card.write_text(original, encoding="utf-8")
    assert not r["root"].exists() and not (r["html"] / "index.html").exists()


# ===========================================================================
# 验收① 端到端 + ② 篡改计划拒: plan → apply → verify → rollback(含链接被人改指向后停线不删)
# ===========================================================================

def _serve(directory: Path) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_item1_e2e_plan_apply_verify_rollback(rig):
    from tools.aipos_cli.governance_commit import card_deployment_records
    from tools.aipos_cli.release_target import release_history

    r = rig
    assert _declare_remote(r)[0] == 0
    _auth_revision(r, action="grant", hosts=["devbox", HOST], zones=["production"])
    before = _tree_state(r["prod"])
    rc, out, err = _plan(r, "--tree", r["tree"])
    _show(f"---- 验收① plan exit {rc} ----\n{out}{err}")
    assert rc == 0 and "授权: 就绪" in out
    digest = _plan_hash(out)
    record = next((r["gov"] / "5_tasks" / "records" / "events" / CARD).glob("release_plan_*.md"))
    # ② 篡改计划 = 拒(改计划原文一个字节; 给错哈希 = 拒)
    original = record.read_text(encoding="utf-8")
    record.write_text(original.replace('"index.html"', '"index.htmx"', 1), encoding="utf-8")
    rc, _o, err = _rel(r, "apply", "--task-id", CARD, "--target", TARGET, "--plan-hash", digest, "--actor", EXEC)
    _show(f"[验收② 计划被改] exit {rc}: {err.strip()}")
    assert rc == 1 and "RELEASE_PLAN_MISMATCH" in err and "计划被改" in err
    record.write_text(original, encoding="utf-8")
    rc, _o, err = _rel(r, "apply", "--task-id", CARD, "--target", TARGET, "--plan-hash", "f" * 64, "--actor", EXEC)
    _show(f"[验收② 哈希不符] exit {rc}: {err.strip()}")
    assert rc == 1 and "RELEASE_PLAN_MISMATCH" in err
    assert _tree_state(r["prod"]) == before
    # apply
    rc, out, err = _rel(r, "apply", "--task-id", CARD, "--target", TARGET, "--plan-hash", digest, "--actor", EXEC)
    _show(f"---- 验收① apply exit {rc} ----\n{out}{err}\n假 ssh 记录:\n{r['ssh_log'].read_text()}")
    assert rc == 0 and "APPLY_OK" in out and "只多出本次新建" in out
    release_dir = r["root"] / "releases" / r["audited"]
    assert os.readlink(r["root"] / "current") == f"releases/{r['audited']}"
    assert (release_dir / "assets" / "img" / "logo.svg").read_text() == SITE_FILES["assets/img/logo.svg"]
    assert not (release_dir / "README.md").exists() and not (release_dir / "alias.html").exists()
    for name in ("index.html", "fde.html", "assets"):
        assert os.readlink(r["html"] / name) == f"{r['root']}/current/{name}"
    assert (r["html"] / "index.html").read_text() == SITE_FILES["index.html"]
    after = _tree_state(r["prod"])
    assert {k: v for k, v in after.items() if k in before} == before  # 存量条目一字未变
    deploy = card_deployment_records(r["gov"], CARD)
    meta = f78._git(r["gov"], "status", "--porcelain")
    _show(f"[验收① 部署记录归属本卡] {[p.name for p in deploy]}; 治理根未提交变化: {meta.splitlines()[:4]}")
    assert len(deploy) == 1
    from tools.aipos_cli.frontmatter import require_frontmatter

    fm, body = require_frontmatter(deploy[0])
    _show(f"---- 验收① 部署记录 frontmatter 摘要 ----\noperation={fm['operation']} production_changed={fm['production_changed']} "
          f"commit={fm['commit']} source_tree={fm['source_tree']} exit_code={fm['exit_code']} authorization={fm['authorization']} "
          f"created={len(fm['created'])} baseline_unexpected={fm['baseline_unexpected']}\n{body}")
    assert fm["operation"] == "release" and fm["production_changed"] is True and fm["commit"] == r["audited"]
    assert fm["source_tree"] == r["tree"] and fm["exit_code"] == 0 and fm["baseline_unexpected"] == []
    assert fm["created"][-1] == str(r["html"] / "index.html")  # 首页最后
    assert fm["authorization_ref"] == r["verdict"] and fm["authorization"] == f"{AUTH}@r1"
    # 再 apply 同一计划 = 主机侧同名即停, 零写入, 记录 production_changed=false
    rc, out, err = _rel(r, "apply", "--task-id", CARD, "--target", TARGET, "--plan-hash", digest, "--actor", EXEC)
    _show(f"[验收① 重放同一计划] exit {rc}: {err.strip()[:400]}")
    assert rc == 1 and "TARGET_EXISTS" in err and "production_changed=False" in err
    assert _tree_state(r["prod"]) == after
    # verify(主机侧 + 公网)
    server, base = _serve(r["html"])
    try:
        rc, out, err = _rel(r, "verify", "--task-id", CARD, "--target", TARGET, "--fetch", f"{base}/index.html=index.html",
                            "--fetch", f"{base}/assets/app.css=assets/app.css")
    finally:
        server.shutdown()
    _show(f"---- 验收① verify exit {rc} ----\n{out}{err}")
    assert rc == 0 and "VERIFY_OK" in out and out.count("✓ public") == 2
    # rollback: 首页链接被人改指向 = 停线不删
    (r["html"] / "index.html").unlink()
    (r["html"] / "index.html").symlink_to(r["html"] / "qr.html")
    rc, out, err = _rel(r, "rollback", "--task-id", CARD, "--target", TARGET, "--actor", EXEC)
    _show(f"[验收① rollback 遇被改链接] exit {rc}: {err.strip()}")
    assert rc == 1 and "RELEASE_ROLLBACK_STOPPED" in err and "停线不删" in err
    assert os.readlink(r["html"] / "fde.html") == f"{r['root']}/current/fde.html"  # 首页优先: 停在首页, 其后各项未删
    (r["html"] / "index.html").unlink()
    (r["html"] / "index.html").symlink_to(f"{r['root']}/current/index.html")
    rc, out, err = _rel(r, "rollback", "--task-id", CARD, "--target", TARGET, "--actor", EXEC)
    _show(f"---- 验收① rollback exit {rc} ----\n{out}{err}")
    assert rc == 0 and "ROLLBACK_OK" in out
    assert not any(os.path.lexists(r["html"] / n) for n in ("index.html", "fde.html", "assets"))
    assert release_dir.is_dir() and (r["root"] / "current").is_symlink()  # 不删 release 目录 / current
    assert _tree_state(r["prod"]) == {**before, **{k: v for k, v in after.items() if k.startswith("opt/kiwiai/web/website")}}
    rc, out, _err = _rel(r, "verify", "--task-id", CARD, "--target", TARGET)
    _show(f"[验收① 回退后 verify] exit {rc}: {out.strip().splitlines()[-1]}")
    assert rc == 3 and "VERIFY_MISMATCH" in out
    history = release_history(r["gov"], CARD)
    _show("[件⑤ 历史与审计条目]\n" + "\n".join(f"  {h['command']} (exit {h['exit_code']})" for h in history))
    assert sorted(h["command"].split()[2] for h in history) == ["apply", "apply", "plan", "rollback", "rollback"]
    assert len(list((r["gov"] / "5_tasks" / "records" / "events" / CARD).glob("release_rollback_*.md"))) == 2  # 同秒不覆盖


# ===========================================================================
# 件⑤ 开工稿发布节 / 章程句 / ⑤ 静态守卫
# ===========================================================================

def test_item5_kickoff_section_and_charters(rig):
    from tools.aipos_cli.next_resolver import _read_frontmatter, card_workstation_view
    from tools.aipos_cli.release_target import render_kickoff_lines

    r = rig
    gov = r["gov"]
    fm = _read_frontmatter(gov / "5_tasks" / "queue" / "claimed" / f"{CARD.lower()}.md")
    assert render_kickoff_lines(gov, task_id=CARD, fm=fm) == []  # 无发布目标 = 无节(行为不变)
    assert _declare_remote(r)[0] == 0
    lines = render_kickoff_lines(gov, task_id=CARD, fm=fm)
    _show("---- 件⑤ 执行卡开工稿发布节 ----\n" + "\n".join(lines))
    text = "\n".join(lines)
    for verb in ("plan", "apply", "verify", "rollback"):
        assert f"release {verb} --task-id {CARD} --target {TARGET}" in text
    afm = _read_frontmatter(gov / "5_tasks" / "queue" / "pending" / f"{AUDIT_CARD.lower()}.md")
    audit = "\n".join(render_kickoff_lines(gov, task_id=AUDIT_CARD, fm=afm))
    _show("---- 件⑤ 审计卡开工稿发布节 ----\n" + audit)
    assert f"release verify --task-id {CARD} --target {TARGET}" in audit and "release apply" not in audit and "永不 apply / rollback" in audit
    view = card_workstation_view(gov, CARD, fm)
    assert any("release plan" in ln for ln in view["validation_root_lines"]) and not view.get("validation_root_refusal")
    nozone = dict(fm, authorization_zones=[])
    assert render_kickoff_lines(gov, task_id=CARD, fm=nozone) == []
    ex = (REPO_ROOT / "agents/roles/executor/AGENTS.md").read_text(encoding="utf-8")
    au = (REPO_ROOT / "agents/roles/auditor/AGENTS.md").read_text(encoding="utf-8")
    assert "**生产发布只经产品动词**(AIPOS-F251)" in ex and "禁裸 ssh / scp / rsync 写生产主机" in ex
    assert "**发布卡只核不写**(AIPOS-F251)" in au and "永不 apply / rollback" in au


FORBIDDEN_WORDS = ("sudo", "doas", "systemctl", "service ", "reload", "restart", "rsync", "--delete", "rm -rf", "nginx", "chown", "chmod")
FORBIDDEN_CALLS = {"rename", "replace", "rmtree", "move", "chown", "chmod", "removedirs", "rmdir", "remove", "system", "popen", "lchown"}


def test_item5_static_guard_no_privilege_reload_or_delete():
    """验收⑤: 发布实现(治理根侧 + 主机侧)不存在 sudo / reload / restart / rsync --delete 等调用; 删除只有 rollback 的 os.unlink(链接本身)。"""
    hits: list[str] = []
    for rel in ("tools/aipos_cli/release_target.py", "tools/aipos_cli/release_helper.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(n.body[0].value, ast.Constant)}
        unlinks = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                low = node.value.lower()
                hits += [f"{rel}: 字面量含 {w!r}: {node.value[:60]!r}" for w in FORBIDDEN_WORDS if w in low]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                owner = node.func.value.id
                if owner in ("os", "shutil") and node.func.attr in FORBIDDEN_CALLS:
                    hits.append(f"{rel}:{node.lineno}: 调用 {owner}.{node.func.attr}()")
                if owner == "os" and node.func.attr == "unlink":
                    unlinks += 1
                if owner == "subprocess" and any(k.arg == "shell" for k in node.keywords):
                    hits.append(f"{rel}:{node.lineno}: subprocess shell=")
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[0].value, str):
                head = node.elts[0].value
                if head in ("sudo", "su", "doas", "systemctl", "service", "rsync", "nginx", "rm", "mv", "cp", "scp", "chmod", "chown"):
                    hits.append(f"{rel}:{node.lineno}: 命令头 {head}")
        _show(f"[验收⑤ 静态守卫] {rel}: os.unlink 调用 {unlinks} 处")
        assert unlinks == (1 if rel.endswith("release_helper.py") else 0)
    src = (REPO_ROOT / "tools/aipos_cli/release_helper.py").read_text(encoding="utf-8")
    assert "O_EXCL" in src and "os.mkdir(path, DIR_MODE)" in src and "makedirs" not in src and "os.symlink(item[\"target\"], path)" in src
    _show(f"[验收⑤ 静态守卫] 命中 {hits or '无'}")
    assert not hits, hits


def test_helper_pinned_sha_mismatch_refused(rig):
    """主机侧引导钉 sha256: 下发内容与钉的摘要不符 = 拒, 不执行。"""
    from tools.aipos_cli import release_target as rt

    src = rt.helper_source()
    proc = subprocess.run(["python3", "-I", "-c", rt.bootstrap_code("0" * 64)], input=f"{len(src)}\n".encode() + src + b"{}",
                          capture_output=True, timeout=60)
    _show(f"[helper 钉摘要不符] exit {proc.returncode}: {proc.stdout.decode().strip()}")
    assert proc.returncode == 3 and "HELPER_SHA256_MISMATCH" in proc.stdout.decode()


def test_f251_fixture_discovered_by_runall():
    from tools.aipos_cli.workspace_config import default_test_contract, is_test_file

    assert is_test_file("tests/test_aipos_f251_release_surface.py", default_test_contract())
