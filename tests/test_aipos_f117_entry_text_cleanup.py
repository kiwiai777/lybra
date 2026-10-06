"""AIPOS-F117: 入口与文字收尾——看板导入收敛到 project new、过时文案改读声明、文档棘轮扩面
(gap #53/#52/#51/#54/#57/#62/#65/#75/#78)。

① 看板「导入既有项目」(Owner 10-04 裁定 gap #53): 建项目 = project_structure.import_project_structure →
   workspace_config.scaffold_project(`lybra project new` 唯一实现), 落点 = <home 根>/<项目名>(home 根同 project new 的
   AIPOS-226 优先级梯), 不再建 ~/.lybra/workspaces/。靶场 = 真起看板进程(临时端口 + 临时 HOME + 临时 home 根 + 临时
   repo-root), 经 HTTP 登录后 POST /api/project-structure/import(目录模式 + 文件模式), 断言产物与 project new 同形、
   ~/.lybra/workspaces 未出现; `lybra project import` CLI 同一实现。gap #52: v1 写入方 write_workspace_config /
   default_workspace_config 删除; serve 与看板读 .lybra/config.json 同一读取口 workspace_runtime_config。
② 过时文案: BRANCH_WRONG_BASE 出口教 merge(读 transitions N5.branch_integration.base_sync, gap #75);
   dispatch_exception 改读驱动方(gap #57); lybra_loop.launch 条件⑥ 与 enums harness 描述按卡号取 kickoff(gap #65);
   F110 拉起声明不再提 kickoff_safe(gap #78, F103 夹具豁免撤销); gap #54 两处(tools.py workspace_init 注释、agent watch
   --gate-url help)已由 AIPOS-F101/F103 消解, 此处守不回流。
③ roles.schema custom_roles 不再手写 builtin_classes(漏 advisor), 内置类 = roles[] 全集; 队列终态读 enums terminal
   声明, withdrawn 卡推导为终态(gap #62)。
④ 文档棘轮扩面见 tests/test_aipos_f96_docs_retired_practice_ratchet.py(0_control_plane/ 入扫描面); 此处守模板协议文档已删。

只写 pytest tmp_path(临时 HOME / home 根 / repo-root), 看板只起在临时端口并在 finally 内收尸; 不连生产门、不写真实治理根/工位。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LYBRA_BIN = REPO_ROOT / "bin" / "lybra"
sys.path.insert(0, str(REPO_ROOT))

from tools.aipos_cli.project_structure import emit_yaml  # noqa: E402


def _show(line: str) -> None:
    print(line, flush=True)


def _isolated_env(home: Path, gov_home: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LYBRA_", "AIPOS_"))}
    env["HOME"] = str(home)
    env["LYBRA_HOME_ROOT"] = str(gov_home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _source_workspace(root: Path) -> Path:
    """一个「既有项目」: 有 project.json(带代码仓)、治理文档与普通文档。"""
    (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    (root / "governance").mkdir(parents=True)
    (root / "project.json").write_text(json.dumps({"project": "legacy", "code_repo": "/code/legacy", "config_version": 1}),
                                       encoding="utf-8")
    (root / "governance" / "decision_log.md").write_text("# Decisions\n", encoding="utf-8")
    (root / "governance" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    (root / "README.md").write_text("# Legacy\n\nAn existing project.\n", encoding="utf-8")
    return root


def _structure_file(path: Path, name: str = "from-file") -> Path:
    path.write_text(emit_yaml({
        "schema_version": 1,
        "project_name": name,
        "code_repos": ["/code/from-file"],
        "governance_files": {"decision_log": "governance/decision_log.md"},
        "doc_manifest": [{"source_path": "governance/plan.md", "target_path": "governance/plan.md", "kind": "governance"}],
    }), encoding="utf-8")
    return path


def _assert_project_new_shape(root: Path, name: str, code_repo: str) -> None:
    from tools.aipos_cli.task_loader import QUEUE_SKELETON_STATES, queue_root_for
    from tools.aipos_cli.workspace_config import governance_paths

    decl = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert decl["project"] == name and decl["code_repo"] == code_repo and decl["config_version"] == 1, decl
    for state in QUEUE_SKELETON_STATES:
        assert (Path(queue_root_for(root)) / state).is_dir(), state
    paths = governance_paths(root)
    assert paths["decision_log"].is_file()
    snaps = [p for p in paths["stage_archive"].glob("*.md") if p.name.lower() != "readme.md"]
    assert snaps, "project new 首份阶段快照「项目创建」"
    assert (root / "migration-checklist.md").is_file()
    # 原第二实现的产物不再出现
    assert not (root / ".lybra" / "config.json").exists()
    assert not (root / "README.md").exists()


# ---------------------------------------------------------------------------
# ① 看板导入靶场(真看板进程, 临时端口)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, *, body: dict | None = None, cookie: str | None = None) -> tuple[int, dict, str | None]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}"), resp.headers.get("Set-Cookie")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}"), None


@pytest.fixture()
def board_range(tmp_path):
    home = tmp_path / "home"
    gov_home = tmp_path / "govhome"
    repo_root = tmp_path / "board-ws"
    for d in (home, gov_home, repo_root / "5_tasks" / "queue" / "pending", repo_root / ".lybra"):
        d.mkdir(parents=True)
    token = "f117-owner-probe-token"
    fp = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    (repo_root / ".lybra" / "connection.json").write_text(json.dumps({
        "config_version": 1, "tokens": [{"role": "owner", "token_ref": "f117-owner", "fingerprint": fp, "scopes": []}],
    }), encoding="utf-8")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "web.board.app", "--host", "127.0.0.1", "--port", str(port), "--repo-root", str(repo_root)],
        cwd=str(REPO_ROOT), env=_isolated_env(home, gov_home), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(200):  # 有界: 等看板监听(≤20s)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                assert proc.poll() is None, proc.stdout.read() if proc.stdout else ""
                time.sleep(0.1)
        status, body, set_cookie = _request(f"{base}/api/auth/login", body={"token": token})
        assert status == 200 and set_cookie, (status, body)
        cookie = set_cookie.split(";", 1)[0]
        yield {"base": base, "cookie": cookie, "home": home, "gov_home": gov_home, "repo_root": repo_root, "tmp": tmp_path}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout:
            proc.stdout.close()
    assert proc.poll() is not None  # 看板进程已收尸, 无孤儿


def test_item1_board_import_directory_mode_goes_through_project_new(board_range):
    r = board_range
    source = _source_workspace(r["tmp"] / "existing-project")
    status, body, _ = _request(f"{r['base']}/api/project-structure/import", cookie=r["cookie"], body={
        "mode": "directory", "workspace_path": str(source), "project_id": "imported-dir", "label_en": "Imported Dir"})
    _show(f"[① 看板导入·目录模式] POST /api/project-structure/import → {status}\n"
          + json.dumps({k: body.get(k) for k in ("ok", "operation", "project_name", "home_root", "home_root_source",
                                                 "project_root", "created_by", "migration_item_count",
                                                 "board_config_updated", "mode")}, ensure_ascii=False, indent=1))
    assert status == 200 and body["ok"] is True, body
    root = r["gov_home"] / "imported-dir"
    assert body["project_root"] == str(root) and body["home_root_source"] == "环境变量 LYBRA_HOME_ROOT"
    assert "scaffold_project" in body["created_by"]
    _assert_project_new_shape(root, "imported-dir", "/code/legacy")
    assert not (r["home"] / ".lybra" / "workspaces").exists(), "看板不再自建 ~/.lybra/workspaces/"
    reg = json.loads((r["repo_root"] / ".lybra" / "board_config.json").read_text(encoding="utf-8"))
    assert reg["workspaces"] == [{"label": "Imported Dir", "root": str(root), "label_en": "Imported Dir"}], reg
    assert sorted(p.name for p in source.rglob("*")) == sorted(
        ["5_tasks", "queue", "pending", "governance", "decision_log.md", "notes.md", "project.json", "README.md"])  # 源目录只读
    _show(f"[① 看板导入·目录模式] 项目根 {root} 内容: {sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())}")
    _show(f"[① 看板导入·目录模式] 临时 HOME 下 .lybra/workspaces 存在? {(r['home'] / '.lybra' / 'workspaces').exists()}")

    # 同名再导入 = 拒(project new 同一拒因), 不覆盖
    status2, body2, _ = _request(f"{r['base']}/api/project-structure/import", cookie=r["cookie"], body={
        "mode": "directory", "workspace_path": str(source), "project_id": "imported-dir"})
    _show(f"[① 看板导入·同名再导入] → {status2} {body2.get('error_code')} {body2.get('error_detail') or body2.get('message')}")
    assert body2["ok"] is False and body2["error_code"] == "workspace_not_empty", body2


def test_item1_board_import_file_mode_and_corrupt_registry_fail_closed(board_range):
    r = board_range
    sf = _structure_file(r["tmp"] / "structure.yaml")
    status, body, _ = _request(f"{r['base']}/api/project-structure/import", cookie=r["cookie"], body={
        "mode": "file", "structure_file_path": str(sf), "project_id": "imported-file"})
    _show(f"[① 看板导入·文件模式] → {status} ok={body.get('ok')} project_root={body.get('project_root')}")
    assert status == 200 and body["ok"] is True, body
    _assert_project_new_shape(r["gov_home"] / "imported-file", "imported-file", "/code/from-file")
    assert not (r["home"] / ".lybra" / "workspaces").exists()

    # 登记文件读坏 = 拒(不再吞错后整文件重写丢登记), 且未建项目
    reg = r["repo_root"] / ".lybra" / "board_config.json"
    reg.write_text("{not json", encoding="utf-8")
    status3, body3, _ = _request(f"{r['base']}/api/project-structure/import", cookie=r["cookie"], body={
        "mode": "file", "structure_file_path": str(sf), "project_id": "imported-file-2"})
    _show(f"[① 看板导入·登记文件坏] → {status3} ok={body3.get('ok')} {body3.get('error_code')}")
    assert body3["ok"] is False and reg.read_text(encoding="utf-8") == "{not json"
    assert not (r["gov_home"] / "imported-file-2").exists()


def test_item1_import_reuses_scaffold_project_single_implementation(tmp_path, monkeypatch):
    """导入建项目只经 scaffold_project(打桩计数); 产品代码再无 ~/.lybra/workspaces 落点。"""
    from tools.aipos_cli import project_structure, workspace_config

    calls: list[tuple] = []
    real = workspace_config.scaffold_project

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(workspace_config, "scaffold_project", spy)
    result = project_structure.import_project_structure(_structure_file(tmp_path / "s.yaml", "spy-proj"), tmp_path / "gh",
                                                        actor="f117.probe")
    assert result["ok"] and len(calls) == 1, (result, calls)
    assert calls[0][0][1] == "spy-proj" and calls[0][1]["registered_by"] == "f117.probe"
    src = (REPO_ROOT / "tools" / "aipos_cli" / "project_structure.py").read_text(encoding="utf-8")
    for retired in ("STANDARD_FIVE_PIECE = ", "LYBRA_IGNORE_PATTERNS = ", '"workspace_root": "."', "def import_project_from_yaml"):
        assert retired not in src, retired
    proc = subprocess.run(["git", "grep", "-n", "-F", '"workspaces" / ', "--", "tools", "web", ":!**/tests/**"],
                          cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout  # 1 = 无命中


def test_item1_cli_project_import_same_implementation(tmp_path):
    home, gov_home = tmp_path / "home", tmp_path / "govhome"
    home.mkdir()
    sf = _structure_file(tmp_path / "structure.yaml", "cli-imported")
    env = _isolated_env(home, gov_home)
    env.pop("LYBRA_HOME_ROOT")
    base = ["node", str(LYBRA_BIN), "project", "import", str(sf), "--home-root", str(gov_home), "--actor", "f117.cli"]
    dry = subprocess.run(base + ["--dry-run"], cwd=str(home), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                         text=True, timeout=120)
    _show(f"[① lybra project import --dry-run] exit {dry.returncode}\n{dry.stdout}{dry.stderr}")
    assert dry.returncode == 0 and not (gov_home / "cli-imported").exists()
    real = subprocess.run(base, cwd=str(home), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    _show(f"[① lybra project import] exit {real.returncode}\n{real.stdout}{real.stderr}")
    assert real.returncode == 0, real.stderr
    _assert_project_new_shape(gov_home / "cli-imported", "cli-imported", "/code/from-file")
    assert json.loads((gov_home / "cli-imported" / "project.json").read_text(encoding="utf-8"))["registered_by"] == "f117.cli"
    assert not (home / ".lybra" / "workspaces").exists()


def test_item1_v1_writer_retired_and_config_read_single_source(tmp_path):
    """gap #52: v1 写入方删除; serve(_config_defaults)与看板(_runtime_config_defaults)同经 workspace_runtime_config。"""
    from tools.aipos_cli import aipos_cli, workspace_config
    from web.board import app

    assert not hasattr(workspace_config, "write_workspace_config")
    assert not hasattr(workspace_config, "default_workspace_config")
    for rel in ("tools/aipos_cli/aipos_cli.py", "web/board/app.py"):
        assert "workspace_runtime_config(" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel
    ws = tmp_path / "ws"
    (ws / ".lybra").mkdir(parents=True)
    assert aipos_cli._config_defaults(ws)["config_path"] is None  # 文件缺 = 全缺省
    assert aipos_cli._config_defaults(ws)["mcp_port"] == workspace_config.DEFAULT_MCP_PORT
    (ws / ".lybra" / "config.json").write_text(json.dumps({"board": {"port": 1}, "mcp": {"transport_token_env": "X_TOK"}}),
                                               encoding="utf-8")
    cli_view, board_view = aipos_cli._config_defaults(ws), app._runtime_config_defaults(ws)
    assert cli_view["board_port"] == board_view["board_port"] == 1
    assert cli_view["transport_token_env"] == board_view["transport_token_env"] == "X_TOK"
    assert board_view["config_path"] == ".lybra/config.json"
    (ws / ".lybra" / "config.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError):
        aipos_cli._config_defaults(ws)  # 启动路径 fail-closed
    broken = app._runtime_config_defaults(ws)
    assert broken["config_error"] and broken["board_port"] == workspace_config.DEFAULT_BOARD_PORT  # 状态面报错不抛


def test_item1_board_login_url_reads_same_config_reader(tmp_path, capsys):
    """看板登录地址(board_login.resolve_board_url)的 .lybra/config.json 段同经 workspace_runtime_config; 读坏不再静默。"""
    from tools.aipos_cli.board_login import resolve_board_url

    ws, conn = tmp_path / "ws", tmp_path / "connection.json"
    (ws / ".lybra").mkdir(parents=True)
    conn.write_text(json.dumps({"board": {"url": "http://conn.example:1/"}}), encoding="utf-8")
    cfg = ws / ".lybra" / "config.json"
    cfg.write_text(json.dumps({"board": {"url": "http://ws.example:2/"}}), encoding="utf-8")
    assert resolve_board_url(conn, workspace_root=ws) == "http://ws.example:2"
    cfg.write_text(json.dumps({"board": {"host": "10.0.0.1", "port": 3}}), encoding="utf-8")
    assert resolve_board_url(conn, workspace_root=ws) == "http://10.0.0.1:3"
    cfg.write_text(json.dumps({"mcp": {}}), encoding="utf-8")  # 未写 board 段 = 回退 connection.json(原行为)
    assert resolve_board_url(conn, workspace_root=ws) == "http://conn.example:1"
    cfg.write_text("{bad", encoding="utf-8")
    assert resolve_board_url(conn, workspace_root=ws) == "http://conn.example:1"
    assert "Warning:" in capsys.readouterr().err  # 读坏出声后回退, 不静默
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_login.py").read_text(encoding="utf-8")
    assert "workspace_runtime_config(" in src and "CONFIG_RELATIVE_PATH" not in src


# ---------------------------------------------------------------------------
# ② 过时文案改读声明 / 改正
# ---------------------------------------------------------------------------


def _schema(name: str) -> dict:
    return json.loads((REPO_ROOT / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_item2_branch_wrong_base_exit_reads_base_sync_declaration(monkeypatch):
    from tools.aipos_cli import board_adapter
    from tools.schema_loader import SchemaLoadError

    decl = _schema("transitions")["nodes"]["N5"]["branch_integration"]["base_sync"]
    assert "merge" in decl["command"] and "rebase" not in decl["command"]
    rendered = board_adapter.base_sync_command("card/X-1", "main")
    _show(f"[② BRANCH_WRONG_BASE 出口] {rendered}")
    assert rendered == "git checkout card/X-1 && git merge main"
    src = (REPO_ROOT / "tools" / "aipos_cli" / "board_adapter.py").read_text(encoding="utf-8")
    assert "git rebase" not in src
    import tools.schema_loader as sl

    monkeypatch.setattr(sl, "get_branch_integration", lambda *a, **k: {"base_branch": "main"})
    with pytest.raises(SchemaLoadError):
        board_adapter.base_sync_command("card/X-1", "main")  # 声明缺 = fail-closed, 不回落写死命令


def test_item2_declaration_texts_current():
    transitions, verbs, enums = _schema("transitions"), _schema("verbs"), _schema("enums")
    exc = transitions["record_authenticity"]["submission_identity"]["dispatch_exception"]
    assert "驱动方" in exc and "owner-dispatch" not in exc  # gap #57
    launch = json.dumps(verbs, ensure_ascii=False)
    assert "next_card.task_id = 等待目标卡" not in launch and "kickoff_safe" not in launch  # gap #65 / #78
    assert "--task-id <等待目标卡>" in launch
    harness = enums["enums"]["harness"]["description"]
    assert "my-tasks --task-id <等待目标卡>" in harness and "= my-tasks next_card.kickoff 原文" not in harness  # gap #65
    f103 = (REPO_ROOT / "tests" / "test_aipos_f103_retire_connector_single_envelope.py").read_text(encoding="utf-8")
    assert '("kickoff_safe", "schema/verbs.schema.json")' not in f103  # gap #78 豁免撤销


def test_item2_gap54_stays_resolved():
    tools_py = (REPO_ROOT / "tools" / "mcp_server" / "tools.py").read_text(encoding="utf-8")
    assert "workspace_init" not in tools_py
    proc = subprocess.run([sys.executable, "-m", "tools.aipos_cli.aipos_cli", "agent", "watch", "--help"], cwd=str(REPO_ROOT),
                          env={**os.environ, "PYTHONPATH": str(REPO_ROOT)}, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "--gate-url" not in proc.stdout, proc.stdout


# ---------------------------------------------------------------------------
# ③ roles builtin 类单源 + 终态读声明
# ---------------------------------------------------------------------------


def test_item3_custom_role_builtin_classes_single_source():
    from tools.aipos_cli.custom_roles import _builtin_role_names, validate_builtin_class

    roles = _schema("roles")
    assert "builtin_classes" not in roles["custom_roles"]  # 手写第二份(漏 advisor)已删
    declared = {spec["role"] for spec in roles["roles"]}
    assert "advisor" in declared and _builtin_role_names() == declared
    assert validate_builtin_class("advisor") == (True, None)
    import tools.schema_loader as sl

    assert not hasattr(sl, "get_builtin_role_classes")


def test_item3_withdrawn_is_terminal_by_declaration(tmp_path):
    from tools.aipos_cli import task_loader
    from tools.aipos_cli.next_resolver import derive_next_step
    from tools.aipos_cli.workspace_config import scaffold_project

    values = _schema("enums")["enums"]["queue_state"]["values"]
    assert all(isinstance(v.get("terminal"), bool) for v in values)
    assert task_loader.QUEUE_TERMINAL_STATES == tuple(v["value"] for v in values if v["terminal"]) == ("completed", "withdrawn")
    root = scaffold_project(tmp_path / "gh", "probe")
    wdir = Path(task_loader.queue_root_for(root)) / "withdrawn"
    wdir.mkdir(parents=True)
    (wdir / "probe-w1.md").write_text("---\ntask_id: PROBE-W1\ntitle: w\nstatus: withdrawn\ntask_mode: code\n---\n# w\n",
                                      encoding="utf-8")
    result = derive_next_step("PROBE-W1", root)
    _show(f"[③ withdrawn 推导] {json.dumps(result, ensure_ascii=False)}")
    assert result["derivable"] is True and result["current_state"] == "withdrawn" and result["triggered_by"] == "none"
    assert not result["missing_records"]


# ---------------------------------------------------------------------------
# ④ 已删模板/init 的协议文档不回流 + 登记
# ---------------------------------------------------------------------------


def test_item4_template_protocol_doc_removed():
    assert not (REPO_ROOT / "0_control_plane" / "templates" / "workspace_template_protocol.md").exists()
    f105 = (REPO_ROOT / "tests" / "test_aipos_f105_single_project_entry.py").read_text(encoding="utf-8")
    assert "LANE_BLOCKED: set[str] = set()" in f105


def test_fixture_registered_in_runall():
    runall = (REPO_ROOT / "tests" / "run-all.sh").read_text(encoding="utf-8")
    assert "run_pytest \"tests/test_aipos_f117_entry_text_cleanup.py\"" in runall
    assert subprocess.run(["bash", "-n", str(REPO_ROOT / "tests" / "run-all.sh")]).returncode == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
