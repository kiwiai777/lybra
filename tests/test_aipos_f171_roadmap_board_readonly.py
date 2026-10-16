"""AIPOS-F171 — 路线图数据底座 + 看板只读(gap #155 / #156)。

件① 声明: 治理根 roadmap.yaml(落点 project.json paths.roadmap_file, 缺省 governance/roadmap.yaml; 形声明 config.schema
    governance_structure.roadmap)。`lybra roadmap show / add-milestone / add-criterion / set / remove`: 写命令两阶段(缺省预演零写入;
    --confirm 写 + decision_log 指针 + 经 post_write_landing.commands.roadmap_writers 落账); 删除只允许无挂接卡; 坏文件按声明拒。
件② 卡挂接: card.schema milestone / criteria + roadmap_link; 出卡校验(引用不存在 = 拒, 未挂 = 游离卡警告, 无路线图 = 不校验);
    `lybra queue amend` 回挂老卡(只改挂接字段, 任一声明状态)。游离判定唯一函数 roadmap.card_link_status。
件③ 看板只读: 除鉴权通道外的写请求一律 405 BOARD_READ_ONLY(声明 verbs.schema board_readonly), 原 POST 业务路由表下线;
    项目列表 = home 根推导(owner_inbox.home_projects, 暂停标注), board_config 退役不读; project-map.md 读取下线, 项目页里程碑读路线图。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓, 治理根在仓内子目录)。项目名 / 实例名 / 卡号均为靶场值(产品不写死)。
全程不读真实凭据、不碰真实治理根、不对真实门写。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场构件唯一来源

PROJECT = "f171-probe"              # 靶场项目名(非真实项目)
ADVISOR = f"advisor.{PROJECT}.h1"

#: 下线的看板 POST 业务路由(原 _api_post_routes 全表)
RETIRED_POST_ROUTES = (
    "/api/project-structure/preview", "/api/project-structure/import", "/api/parent-requirement/preview",
    "/api/planner-tick/preview", "/api/planner-tick/manual-flow/preview", "/api/planner-draft/review",
    "/api/forum-event/review", "/api/owner-decision/resolve/review", "/api/planner-draft/publish/dry-run",
    "/api/ai-author/preview", "/api/ai-author/confirm", "/api/ai-author/live/preview", "/api/ai-author/live/confirm",
    "/api/agent-profile/draft", "/api/agent-profile/confirm", "/api/execute/dry-run", "/api/execute/confirm",
    "/api/verify/approve", "/api/verify/reject",
)


def _show(text: str) -> None:
    print(text, flush=True)


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-c", "core.quotepath=false", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
                          cwd=str(repo), check=True, capture_output=True, text=True).stdout.strip()


def _cli(*argv: str) -> tuple[int, str, str]:
    from tools.aipos_cli.aipos_cli import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = main(list(argv))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue(), err.getvalue()


def _tree_digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.relative_to(root).parts}


def _write_card(gov: Path, state: str, task_id: str, extra: dict | None = None) -> Path:
    from tools.aipos_cli.record_writer import render_markdown

    meta = {"task_id": task_id, "title": f"{task_id} probe", "status": state, **(extra or {})}
    path = gov / "5_tasks" / "queue" / state / f"{task_id.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(meta, f"# {task_id}\n"), encoding="utf-8")
    return path


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """home 根 = 治理仓(带 origin 裸仓); 治理根 = <home>/<PROJECT>(仓内子目录)。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", str(hroot), "--actor", ADVISOR)
    assert rc == 0, out + err
    remote = init_governance_repo(hroot)
    return {"home": hroot, "gov": hroot / PROJECT, "remote": remote, "tmp": tmp_path}


def _rm(rig, *argv: str) -> tuple[int, str, str]:
    return _cli("roadmap", *argv[:1], "--workspace-root", str(rig["gov"]), *argv[1:])


# ===========================================================================
# 件① 声明与编辑命令
# ===========================================================================

def test_item1_declarations_single_source():
    from tools.aipos_cli import roadmap as rm
    from tools.aipos_cli.governance_commit import post_write_landing_declaration
    from tools.aipos_cli.workspace_config import PROJECT_PATH_KEYS, _project_paths_declaration
    from tools.schema_loader import load_schema

    decl = rm.roadmap_declaration()
    assert decl["path_key"] == "roadmap_file" and "roadmap_file" in PROJECT_PATH_KEYS
    assert _project_paths_declaration()["roadmap_file"]["default"] == "governance/roadmap.yaml"
    two_phase = load_schema("verbs")["two_phase_protocol"]["roadmap_writers"]
    assert tuple(two_phase["commands"]) == rm.SUBCOMMANDS
    landing = post_write_landing_declaration()["commands"]["roadmap_writers"]
    assert landing["subcommands"] == list(rm.SUBCOMMANDS) and "{actor}" in landing["message_template"]
    link = rm.link_declaration()
    card = load_schema("card")
    assert set(link["fields"]) <= set(card["fields"]) and set(link["fields"]) <= set(card["advisor_zone"]["fields"])


def test_item1_show_without_roadmap_and_add_preview_confirm_landing_pointer(rig):
    home, gov = rig["home"], rig["gov"]
    rc, out, err = _rm(rig, "show")
    _show(f"[件①·show 无路线图] rc={rc}\n{out}{err}")
    assert rc == 0 and "未声明路线图" in out and "add-milestone" in out

    before, head_before = _tree_digest(home), _git(home, "rev-parse", "HEAD")
    argv = ("add-milestone", "--id", "M1", "--lane", "core", "--title", "第一阶段可交付", "--target-date", "2026-11-30")
    rc, out, err = _rm(rig, *argv)
    _show(f"[件①·add-milestone 预演] rc={rc}\n{out}{err}")
    assert rc == 0 and "预览" in out and "+- id: M1" in out and "decision_log 指针(将生成)" in out
    assert (_tree_digest(home), _git(home, "rev-parse", "HEAD")) == (before, head_before), "预演零写入、不落账"

    rc, out, err = _rm(rig, *argv, "--confirm", "--actor", ADVISOR)
    msg = _git(home, "log", "-1", "--format=%B")
    names = _git(home, "show", "--name-only", "--format=", "HEAD").splitlines()
    pushed = _git(home, "rev-parse", "HEAD") == _git(rig["remote"], "rev-parse", "main")
    _show(f"[件①·add-milestone --confirm] rc={rc}\n{out}{err}[git log -1]\n{msg}\n[git show --name-only HEAD] {names}\npushed={pushed}")
    assert rc == 0 and "已写入" in out and "落账: verdict=PASS" in out
    assert "路线图变更落账 add-milestone M1" in msg and f"Actor: {ADVISOR}" in msg and pushed
    assert f"{PROJECT}/governance/roadmap.yaml" in names
    pointer_rel = next(n for n in names if "decision_log" in n)
    pointer = (home / pointer_rel).read_text(encoding="utf-8")
    _show(f"[件①·decision_log 指针原文 {pointer_rel}]\n{pointer}")
    assert "decision_type: auto_pointer" in pointer and "**Record**: `governance/roadmap.yaml`" in pointer
    assert "lybra roadmap add-milestone --confirm" in pointer and f"**Decided by**: {ADVISOR}" in pointer

    rc, out, err = _rm(rig, "add-criterion", "--milestone", "M1", "--id", "M1-C1", "--statement", "端到端跑通一张卡",
                       "--verify", "auto", "--confirm", "--actor", ADVISOR)
    assert rc == 0, out + err
    rc, out, err = _rm(rig, "set", "--id", "M1", "--field", "status", "--value", "active", "--confirm", "--actor", ADVISOR)
    _show(f"[件①·set --confirm] rc={rc}\n{out}{err}")
    assert rc == 0 and "M1.status: None → 'active'" in out
    rc, out, err = _rm(rig, "set", "--id", "M1", "--field", "status", "--value", "active")
    assert rc == 0 and "无改动" in out, out + err  # 同值 = 无改动
    rc, out, err = _rm(rig, "set", "--id", "M1", "--field", "id", "--value", "M9")
    _show(f"[件①·set id 被拒] rc={rc}\n{out}{err}")
    assert rc == 1 and "ROADMAP_FIELD_NOT_SETTABLE" in err
    rc, out, err = _rm(rig, "add-milestone", "--id", "M1-C1", "--lane", "core", "--title", "dup")
    assert rc == 1 and "ROADMAP_ID_DUPLICATE" in err
    text = (gov / "governance" / "roadmap.yaml").read_text(encoding="utf-8")
    _show(f"[件①·roadmap.yaml 原文]\n{text}")
    assert "status: active" in text and "verify: auto" in text
    pointers = sorted((gov / "governance" / "decision_log").rglob("*roadmap-*-auto-pointer.md"))
    assert len(pointers) == 3, pointers  # 每次确认改动各一份指针
    assert _git(home, "status", "--porcelain", "-uall") == ""


def test_item1_remove_refused_when_cards_link(rig):
    gov = rig["gov"]
    for argv in (("add-milestone", "--id", "M1", "--lane", "core", "--title", "t1"),
                 ("add-criterion", "--milestone", "M1", "--id", "M1-C1", "--statement", "s1", "--verify", "artifact"),
                 ("add-criterion", "--milestone", "M1", "--id", "M1-C2", "--statement", "s2", "--verify", "owner_check")):
        rc, out, err = _rm(rig, *argv, "--confirm", "--actor", ADVISOR)
        assert rc == 0, out + err
    _write_card(gov, "completed", "F171P-1", {"milestone": "M1", "criteria": ["M1-C1"]})
    before = (gov / "governance" / "roadmap.yaml").read_text(encoding="utf-8")
    for target in ("M1", "M1-C1"):
        rc, out, err = _rm(rig, "remove", "--id", target, "--confirm", "--actor", ADVISOR)
        _show(f"[件①·remove {target} 有挂接卡] rc={rc}\n{out}{err}")
        assert rc == 1 and "ROADMAP_REMOVE_LINKED" in err and "F171P-1(completed)" in err
    assert (gov / "governance" / "roadmap.yaml").read_text(encoding="utf-8") == before, "拒删零写入"
    rc, out, err = _rm(rig, "remove", "--id", "M1-C2", "--confirm", "--actor", ADVISOR)
    _show(f"[件①·remove 无挂接标准] rc={rc}\n{out}{err}")
    assert rc == 0 and "删除标准 M1-C2(无挂接卡)" in out
    rc, out, err = _rm(rig, "set", "--id", "M1", "--field", "status", "--value", "dropped", "--confirm", "--actor", ADVISOR)
    assert rc == 0 and "'dropped'" in out, out + err


def test_item1_invalid_file_refused_by_schema(rig):
    gov = rig["gov"]
    bad = "milestones:\n- id: M1\n  title: no lane\n  status: someday\n  criteria:\n  - id: M1\n    statement: x\n    verify: maybe\n  extra: 1\n"
    (gov / "governance" / "roadmap.yaml").write_text(bad, encoding="utf-8")
    rc, out, err = _rm(rig, "show")
    _show(f"[件①·坏文件 show] rc={rc}\n{out}{err}")
    assert rc == 1 and "ROADMAP_INVALID" in err
    for needle in ("缺 'lane'", "someday", "重复", "maybe", "'extra'"):
        assert needle in err, needle
    rc, out, err = _rm(rig, "add-milestone", "--id", "M2", "--lane", "core", "--title", "t", "--confirm", "--actor", ADVISOR)
    assert rc == 1 and "ROADMAP_INVALID" in err
    assert (gov / "governance" / "roadmap.yaml").read_text(encoding="utf-8") == bad, "坏文件不被改写"
    (gov / "governance" / "roadmap.yaml").unlink()
    rc, out, err = _rm(rig, "add-milestone", "--id", "bad id", "--lane", "core", "--title", "t", "--confirm", "--actor", ADVISOR)
    _show(f"[件①·id 不合式样] rc={rc}\n{out}{err}")
    assert rc == 1 and "不合式样" in err
    assert not (gov / "governance" / "roadmap.yaml").exists(), "拒写不留空文件"


# ===========================================================================
# 件② 卡挂接: 出卡校验 / 无路线图不警告 / amend 回挂
# ===========================================================================

def _draft_meta(task_id: str, **extra) -> dict:
    return {"task_id": task_id, "title": f"{task_id} probe", "project": PROJECT, "assigned_to": "exec.probe", "context_bundle": "default_dev",
            "task_mode": "docs", "priority": "medium", "status": "pending", "created_by": ADVISOR, "needs_owner": False,
            "output_target": "docs/", "artifact_policy": "formal_write", **extra}


def _roadmap_msgs(result: dict) -> list[str]:
    return [m for m in [*result["blocking_reasons"], *result["warnings"]] if "ROADMAP_" in m]


def test_item2_draft_link_exists_missing_and_no_roadmap(rig):
    from tools.aipos_cli.draft_writer import create_draft

    gov = rig["gov"]
    res = create_draft(gov, _draft_meta("F171P-10"), "# body\n", dry_run=True)
    _show(f"[件②·无路线图项目] verdict={res['verdict']} roadmap_msgs={_roadmap_msgs(res)}")
    assert _roadmap_msgs(res) == []  # 无路线图 = 不校验不警告(行为不变)

    for argv in (("add-milestone", "--id", "M1", "--lane", "core", "--title", "t1"),
                 ("add-milestone", "--id", "M2", "--lane", "web", "--title", "t2"),
                 ("add-criterion", "--milestone", "M1", "--id", "M1-C1", "--statement", "s1", "--verify", "auto")):
        assert _rm(rig, *argv, "--confirm", "--actor", ADVISOR)[0] == 0
    linked = create_draft(gov, _draft_meta("F171P-11", milestone="M1", criteria=["M1-C1"]), "# body\n", dry_run=True)
    _show(f"[件②·挂接存在] verdict={linked['verdict']} roadmap_msgs={_roadmap_msgs(linked)}")
    assert _roadmap_msgs(linked) == [] and linked["verdict"] != "BLOCK"
    unknown = create_draft(gov, _draft_meta("F171P-12", milestone="M9", criteria=["M1-C1"]), "# body\n", dry_run=True)
    _show(f"[件②·挂接不存在] verdict={unknown['verdict']} blocking={unknown['blocking_reasons']}")
    assert unknown["verdict"] == "BLOCK"
    blocking = " ".join(unknown["blocking_reasons"])
    assert "ROADMAP_LINK_INVALID" in blocking and "'M9' 不在路线图" in blocking and "属于里程碑 'M1', 不属 'M9'" in blocking
    crit_only = create_draft(gov, _draft_meta("F171P-13", criteria=["M1-C1"]), "# body\n", dry_run=True)
    assert crit_only["verdict"] == "BLOCK" and "须同时给 milestone" in " ".join(crit_only["blocking_reasons"])
    free = create_draft(gov, _draft_meta("F171P-14"), "# body\n", dry_run=True)
    _show(f"[件②·游离卡警告] verdict={free['verdict']} warnings={_roadmap_msgs(free)}")
    assert free["verdict"] != "BLOCK" and any(m.startswith("ROADMAP_UNLINKED") and "M1, M2" in m for m in free["warnings"])

    # publish 同一校验: 写出草稿后经 draft validate(publish 前校验同一 validate_draft_metadata)
    written = create_draft(gov, _draft_meta("F171P-15", milestone="M2"), "# body\n", dry_run=False)
    assert written["wrote"], written
    rc, out, err = _cli("--workspace-root", str(gov), "draft", "validate", "--path", written["target_path"])
    assert "ROADMAP_" not in out + err, out + err


def test_item2_amend_backlinks_old_cards(rig):
    from tools.aipos_cli.board_adapter import amend_task

    gov = rig["gov"]
    for argv in (("add-milestone", "--id", "M1", "--lane", "core", "--title", "t1"),
                 ("add-criterion", "--milestone", "M1", "--id", "M1-C1", "--statement", "s1", "--verify", "auto")):
        assert _rm(rig, *argv, "--confirm", "--actor", ADVISOR)[0] == 0
    card = _write_card(gov, "completed", "F171P-20")
    rc, out, err = _cli("--workspace-root", str(gov), "queue", "amend", "--task-id", "F171P-20", "--actor", ADVISOR,
                        "--amendments", json.dumps({"milestone": "M1", "criteria": ["M1-C1"]}), "--amendment-reason", "回挂路线图")
    _show(f"[件②·amend 回挂 completed 卡] rc={rc}\n{out[:900]}{err}")
    assert rc == 0 and '"ok": true' in out
    text = card.read_text(encoding="utf-8")
    assert "milestone: M1" in text and "- M1-C1" in text and "status: completed" in text
    rc, out, err = _cli("--workspace-root", str(gov), "queue", "amend", "--task-id", "F171P-20", "--actor", ADVISOR,
                        "--amendments", json.dumps({"milestone": "M404"}), "--amendment-reason", "错挂")
    _show(f"[件②·amend 挂不存在里程碑] rc={rc}\n{out[:900]}{err}")
    assert rc == 1 and "ROADMAP_LINK_INVALID" in out and "M404" in out
    assert "milestone: M1" in card.read_text(encoding="utf-8"), "被拒零改写"
    other = amend_task(task_id="F171P-20", actor=ADVISOR, amendments={"priority": "low"}, amendment_reason="x", dry_run=True,
                       repo_root=gov)
    assert other["verdict"] == "BLOCK" and "Only pending" in json.dumps(other)  # 非挂接字段照旧只许 pending
    rc, out, err = _rm(rig, "show")
    _show(f"[件②·show 挂接后] rc={rc}\n{out}{err}")
    assert rc == 0 and "M1-C1 [auto]" in out and "挂接卡 1" in out


def test_item2_card_link_status_single_judgement(tmp_path):
    from tools.aipos_cli.roadmap import card_link_status

    roadmap = {"exists": True, "rel": "governance/roadmap.yaml",
               "milestones": [{"id": "A", "lane": "x", "title": "t", "criteria": [{"id": "A1", "statement": "s", "verify": "auto"}]}]}
    assert card_link_status({}, None)["state"] == "no_roadmap"
    assert card_link_status({}, {"exists": False})["state"] == "no_roadmap"
    assert card_link_status({}, roadmap)["state"] == "unlinked"
    assert card_link_status({"milestone": "A", "criteria": ["A1"]}, roadmap)["state"] == "linked"
    assert card_link_status({"milestone": "B"}, roadmap)["state"] == "invalid"
    assert card_link_status({"milestone": "A", "criteria": "A1"}, roadmap)["state"] == "invalid"


# ===========================================================================
# 件③ 看板只读 + home 根项目列表 + 项目页里程碑简版
# ===========================================================================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board(tmp_path: Path):
    from tools.aipos_cli.workspace_config import scaffold_project
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler

    home = tmp_path / "board-home"
    for name in ("p-alpha", "p-beta"):
        scaffold_project(home, name, registered_by="fixture")
    data = json.loads((home / "p-beta" / "project.json").read_text(encoding="utf-8"))
    data["status"] = "paused"
    (home / "p-beta" / "project.json").write_text(json.dumps(data), encoding="utf-8")
    (home / "p-alpha" / "governance").mkdir(parents=True, exist_ok=True)
    (home / "p-alpha" / "governance" / "roadmap.yaml").write_text(
        "milestones:\n- id: A1\n  lane: core\n  title: Alpha goal\n  criteria:\n  - id: A1-C1\n    statement: holds\n    verify: artifact\n",
        encoding="utf-8")
    (home / "p-alpha" / "governance" / "project-map.md").write_text("---\nmap_version: 1\n---\n", encoding="utf-8")
    store = SessionStore()
    cookie = f"{SESSION_COOKIE_NAME}={store.create(role='owner', scopes=['owner_confirm'])}"
    serve_root = tmp_path / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(repo_root=serve_root, home_root=home, session_store=store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base": f"http://127.0.0.1:{server.server_address[1]}", "cookie": cookie, "home": home}
    finally:
        server.shutdown()
        server.server_close()


def _request(b, path: str, method: str = "GET", body: dict | None = None, cookie: bool = True) -> tuple[int, str]:
    data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json"})
    if cookie:
        req.add_header("Cookie", b["cookie"])
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_item3_every_write_route_answers_405_read_only(board):
    before = _tree_digest(board["home"])
    lines = []
    for route in RETIRED_POST_ROUTES:
        status, body = _request(board, route, "POST", {"task_id": "X", "owner_confirmed": True, "actor": "owner"})
        payload = json.loads(body)
        lines.append(f"POST {route} -> {status} {payload['error']} {payload['message']}")
        assert status == 405 and payload["error"] == "BOARD_READ_ONLY" and payload["message"] == "看板只读, 请经顾问", route
    for method in ("PUT", "PATCH", "DELETE"):
        status, body = _request(board, "/api/queue", method, {})
        lines.append(f"{method} /api/queue -> {status} {json.loads(body)['error']}")
        assert status == 405 and json.loads(body)["error"] == "BOARD_READ_ONLY"
    _show("[件③·写路由逐条(测试客户端)]\n" + "\n".join(lines))
    assert _tree_digest(board["home"]) == before, "只读看板零写入"
    # 鉴权通道照旧(不修鉴权): 错 token 登录 = 鉴权拒, 不是只读 405
    status, body = _request(board, "/api/auth/login", "POST", {"token": "wrong-token"}, cookie=False)
    _show(f"[件③·鉴权通道照旧] POST /api/auth/login(错 token) -> {status} {body[:200]}")
    assert status != 405


def test_item3_declaration_matches_auth_channels_and_post_table_gone():
    from web.board.app import board_readonly_declaration

    src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    assert "_api_post_routes" not in src and "post_routes" not in src
    do_post = src[src.index("def do_POST(self)"):src.index("def do_PUT(self)")]
    handled = re.findall(r'if path == "(/api/auth/[^"]+)"', do_post)
    assert sorted(handled) == sorted(board_readonly_declaration()["allowed_post_paths"])
    for name in ("_owner_verification_approve_route", "_execute_confirm_route", "_project_structure_import_route",
                 "_load_board_config", "get_project_map"):
        assert name not in src, name


def test_item3_project_list_home_root_with_paused_marker(board):
    status, body = _request(board, "/api/overview")
    payload = json.loads(body)
    rows = [(w["label"], w["paused"], w["project_status"]) for w in payload["workspaces"]]
    _show(f"[件③·项目列表 home 根推导] {rows}")
    assert status == 200 and rows == [("p-alpha", False, "active"), ("p-beta", True, "paused")]


def test_item3_project_page_roadmap_simple_view(board):
    status, body = _request(board, "/api/roadmap?workspace=0")
    data = json.loads(body)["data"]
    _show(f"[件③·项目页里程碑简版 /api/roadmap?workspace=0] {json.dumps(data, ensure_ascii=False)}")
    assert status == 200 and data["exists"] and data["milestones"][0]["id"] == "A1"
    assert data["milestones"][0]["criteria"][0]["verify"] == "artifact"
    status, body = _request(board, "/api/roadmap?workspace=1")
    assert status == 200 and json.loads(body)["data"]["exists"] is False  # 无路线图 = 整区隐藏
    status, _body = _request(board, "/api/project-map?workspace=0")
    assert status == 404  # project-map.md 读取下线(文件仍在)
    assert (board["home"] / "p-alpha" / "governance" / "project-map.md").exists()


def test_item3_frontend_hides_write_controls():
    static = REPO_ROOT / "web" / "board" / "static"
    overview = (static / "overview.html").read_text(encoding="utf-8")
    detail = (static / "project-detail.html").read_text(encoding="utf-8")
    index = (static / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<div class="setup-option" data-board-write hidden>', overview)  # 导入(写)整块隐藏
    assert "workspace.paused" in overview and "i18n.t('overview.paused')" in overview
    assert "/api/verify/approve" not in detail and "/api/verify/reject" not in detail and "vb-pass" not in detail
    assert "i18n.t('vb.readonly_hint')" in detail
    for title in ("AI Task Authoring", "Draft Publish", "Controlled Execute", "Draft Create", "Custom Agent Profiles"):
        head = index.index(f"<h2>{title}</h2>")
        assert "data-board-write" in index[index.rindex("<section", 0, head):head], title
    for css in ("overview.css", "styles.css"):
        assert "[data-board-write] { display: none !important; }" in (static / css).read_text(encoding="utf-8")
