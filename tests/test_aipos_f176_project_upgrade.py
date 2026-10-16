"""AIPOS-F176 — 项目升级: `lybra project upgrade` 预演 / 确认 + 看板升级按钮(写例外②) + 「告诉顾问」引导。

验收(卡面 ★验收):
 ① 临时 home(旧版治理根: 缺 record_type 快照 / 缺决策指针 / 章程过期工位 / 缺新配置键 / 无 roadmap / 未声明模型):
    upgrade 预演原文、确认执行原文、幂等重跑原文、中途失败停止原文
 ② 看板: Owner 会话预演 / 确认原文、非 Owner 403、workspace 越界拒、令牌校验六类拒、其他写路由 405(测试客户端)
 ③ 引导: 逐顾问话术原文、做到后消失原文、--guidance CLI 原文
 防碎片化: 步骤清单读声明, 各步只调既有命令函数; 看板写路由只经 F172 同一安全框架 envelope_board.handle_owner_write。

靶场 = 临时 home 根(git 仓 + 临时远端裸仓; 治理根嵌在仓内子目录 = 人肉期接入项目形)+ 临时工位(章程经假门 RepoGate 由产品 sync 渲染后
退回手写形)+ 进程内门替身(F158 InProcessGate)+ 临时部署目录里的更新说明库(运行代码 mode 替成 dev 指向它)+ 测试进程内线程起的看板。
人肉期样本全部自造于临时根(F158 / F160 / F164 同一造法), 不读写任何真实治理根 / 真实工位 / 真实门 / 真实凭据。
"""
from __future__ import annotations

import html
import json
import re
import socket
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

import test_aipos_f158_envelope_landing_record_type_migration as f158  # noqa: E402  (legacy snapshot samples: single source)
import test_aipos_f160_doc_sync_pointer_backfill as f160  # noqa: E402  (pointerless decision samples: single source)
import test_aipos_f164_charter_freshness as f164  # noqa: E402  (hand-written charter sample: single source)
import test_aipos_f95_harness_launch as f95  # noqa: E402  (workstation / land event / fake-gate sync: single source)
from test_aipos_f73d_loop_driver import _write, init_governance_repo  # noqa: E402

PROJECT = "f176-legacy"            # fixture project name (not a real project)
ADV = f"advisor.{PROJECT}.h1"
ADV2 = f"advisor.{PROJECT}.h2"
EXE = f"exec.{PROJECT}.h1"
AUD = f"audit.{PROJECT}.h1"
OWNER = "owner"
NOTICE_ACTION = "收到更新后可跑 `lybra project upgrade --guidance` 看本项目还有哪些新能力没用上"
#: 下线的看板 POST 业务路由抽样 + 升级页只读 GET 路径的写方法
STILL_READONLY_POSTS = ("/api/queue", "/api/execute/confirm", "/api/owner/upgrade", "/api/owner/inbox", "/owner/upgrade")


def _show(text: str) -> None:
    print(text, flush=True)


_git = f160._git


def _cli(*argv: object) -> tuple[int, str, str]:
    return f160._cli(*[str(a) for a in argv])


def _snapshot(home: Path) -> tuple:
    return f160._snapshot(home)


def _upgrade(rig, *extra: object) -> tuple[int, str, str]:
    return _cli("project", "upgrade", PROJECT, "--home-root", rig["home"], *extra)


@pytest.fixture
def urig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """旧版治理根(人肉期接入形): 缺 record_type 快照 / 缺决策指针 / 章程过期工位 / 缺新配置键 / 无 roadmap / 未声明 pi 模型。"""
    from tools.aipos_cli import deploy_runtime
    from tools.aipos_cli.workspace_config import update_project_json

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT",
                "LYBRA_WORKSPACE_ROOT", "LYBRA_DRIVER_INSTANCE", "USER"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", PROJECT, "--home-root", hroot, "--actor", ADV)
    assert rc == 0, out + err
    remote = init_governance_repo(hroot)
    conn = tmp_path / "owner-vault" / "connection.json"  # fixture Owner credential (fake token, outside the repo)
    _write(conn, json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                             "tokens": [{"role": "owner", "token": "fixture-owner-token-f176", "scopes": ["owner_confirm"]}]}))
    gov = hroot / PROJECT
    rig = {"home": hroot, "gov": gov, "remote": remote, "conn": conn, "tmp": tmp_path}
    # 缺决策指针: F160 同一造法(门签三张信封 → 删两张指针 + 往月记录 + 推导不出样本), 已提交
    f160._pointerless_fixture(rig, monkeypatch)
    # 接入: 顾问两个实例(会话目录) + 子 agent 执行者 + 审计工位(本机); 执行方式声明; 去掉新配置键(人肉期 project.json)
    stations = tmp_path / "stations"
    ws_audit = f95._workstation(stations, gov, "f176-auditor", "auditor", AUD)
    for inst, role, where in ((ADV, "advisor", stations / "adv-h1"), (ADV2, "advisor", stations / "adv-h2"),
                              (EXE, "executor", stations / "exec-subagent"), (AUD, "auditor", ws_audit)):
        f95._land(gov, inst, role, where)

    def legacy(data: dict) -> None:
        data["execution"] = {"subagent_executor": EXE, "auditor": AUD}
        for key in ("charter_auto_sync", "launch_resume_max"):
            data.pop(key, None)

    update_project_json(gov, legacy)
    _git(hroot, "add", "-A")
    _git(hroot, "commit", "-q", "-m", "fixture: legacy governance root (enrolled, execution declared)")
    _git(hroot, "push", "-q", "origin", "main")
    # 章程过期工位: 先经产品 sync 渲染(假门 RepoGate), 再退回人肉期手写章程(无渲染指纹)
    f95.sync_fixture_workstations(monkeypatch, gov, ws_audit)
    f164._unrender(ws_audit)
    # 缺 record_type 快照: F158 同一造法(裁决记录已入库, 快照原文直拷滞留未跟踪 + 推导不出 / 不可复原样本)
    paths = f158._seed_legacy_snapshots(rig)
    # 近期卡: 两张代码卡, 都未声明审计运行证据 / 产物
    for i in (1, 2):
        _write(gov / "5_tasks" / "queue" / "pending" / f"f176-c{i}.md",
               f"---\ntask_id: F176-C{i}\ntask_mode: code\ncreated_at: '2026-10-0{i}T00:00:00Z'\n---\n# c{i}\n")
    _git(hroot, "add", "--", f"{PROJECT}/5_tasks/queue")
    _git(hroot, "commit", "-q", "-m", "fixture: recent cards")
    _git(hroot, "push", "-q", "origin", "main")
    # 更新说明库(产品侧部署目录): 一份含顾问须知 actions 的更新说明; 运行代码 mode 替成 dev 指向临时部署目录
    deploy_dir = tmp_path / "product" / ".deploy"
    update_id = "update_20261016T150000Z_f176f176f176"
    _write(deploy_dir / "advisor_updates" / f"{update_id}.json", json.dumps({
        "record_type": "advisor_update", "update_id": update_id, "deploy_commit": "f176f176f176" + "0" * 28, "release": "rel-f176",
        "deployed_at": "2026-10-16T15:00:00Z", "source_project": "lybra", "source_task_id": "AIPOS-F176",
        "entries": [{"task_id": "AIPOS-F176", "audience": ["advisor"], "summary": "新增 lybra project upgrade(预演/确认)与看板升级按钮",
                     "actions": [NOTICE_ACTION], "source": "card"}]}, ensure_ascii=False))
    monkeypatch.setattr(deploy_runtime, "running_code",
                        lambda code_root=None: deploy_runtime.RunningCode(REPO_ROOT, "dev", deploy_dir=deploy_dir))
    return {**rig, "ws_audit": ws_audit, "snap": paths["snap"], "update_id": update_id}


# ===========================================================================
# ① CLI: 预演 / 确认 / 幂等 / 中途失败停止
# ===========================================================================

def test_item1_preview_confirm_idempotent(urig):
    home, gov = urig["home"], urig["gov"]
    before = _snapshot(home)
    ws_agents = (urig["ws_audit"] / "AGENTS.md").read_text(encoding="utf-8")
    rc, out, err = _upgrade(urig)
    _show(f"[①·预演] rc={rc}\n{out}{err}")
    assert rc == 0 and err == ""
    assert _snapshot(home) == before and (urig["ws_audit"] / "AGENTS.md").read_text(encoding="utf-8") == ws_agents, "预演零写入"
    assert "预览(未写; 加 --confirm --actor <实例或 owner>" in out
    assert re.search(r"^\[1/4\] 配置缺省补齐: 待升级 — ", out, re.M)
    assert "会改: project.json 补 charter_auto_sync = true" in out and "会改: project.json 补 launch_resume_max = 2" in out
    assert re.search(r"^\[2/4\] 工位章程同步: 待升级 — ", out, re.M)
    assert f"{AUD} @ {urig['ws_audit']}: stale: " in out and "unrendered" in out and "lybra sync --harness-root" in out
    assert re.search(r"^\[3/4\] 记录类型补齐: 待升级 — ", out, re.M) and f"会改: {urig['snap']}  类型 audit_report_snapshot" in out
    assert re.search(r"^\[4/4\] 决策指针补生成: 待升级 — ", out, re.M) and "envelope-pol_f160_b.md → " in out
    assert "推导不出·不改 3 份(列出不改; 明细: lybra migrate decision-log-pointers --workspace-root" in out
    assert f"    {ADV}: 1 条" in out and f"    {ADV2}: 1 条" in out
    assert "合计: 待升级 4 项(机械, --confirm 一次执行)" in out

    rc, out, err = _upgrade(urig, "--confirm")
    _show(f"[①·--confirm 缺 --actor] rc={rc}\n{out}{err}")
    assert rc == 2 and "--actor" in err and _snapshot(home) == before

    rc, out, err = _upgrade(urig, "--confirm", "--actor", ADV)
    log = _git(home, "log", "--format=%s", "-4").splitlines()
    status = _git(home, "status", "--porcelain", "-uall")
    pushed = _git(home, "rev-parse", "HEAD") == _git(urig["remote"], "rev-parse", "main")
    _show(f"[①·确认] rc={rc}\n{out}{err}[①·git log -4] {log}\n[①·status] {status!r} pushed={pushed}")
    assert rc == 0 and err == ""
    for i, label in enumerate(("配置缺省补齐", "工位章程同步", "记录类型补齐", "决策指针补生成"), 1):
        assert re.search(rf"^\[{i}/4\] {label}: 已执行 — ", out, re.M), label
    assert out.count("落账: verdict=PASS committed=True pushed=True") == 3
    assert log[:3] == ["chore(governance): decision_log 指针补生成 3 份", "chore(governance): 记录类型补齐迁移 1 份",
                       f"chore(governance): 项目配置落账 upgrade {PROJECT}"] and pushed
    assert "结果: 已执行 4 步 · 跳过 0 步 · 失败 0 步 · 未执行 0 步" in out
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    assert data["charter_auto_sync"] is True and data["launch_resume_max"] == 2
    assert f164.RENDER_MARK in (urig["ws_audit"] / "AGENTS.md").read_text(encoding="utf-8")
    # 推导不出 / 不可复原的样本照旧滞留(列出不改)
    for key in ("claims", "misc", "PRB-2"):
        assert key in status

    head = _git(home, "rev-parse", "HEAD")
    rc, out, err = _upgrade(urig, "--confirm", "--actor", ADV)
    _show(f"[①·幂等重跑确认] rc={rc}\n{out}{err}")
    assert rc == 0 and "无事可做: 各步均已是最新 / 不适用" in out and "结果: 已执行 0 步 · 跳过 4 步 · 失败 0 步 · 未执行 0 步" in out
    assert _git(home, "rev-parse", "HEAD") == head and "落账" not in out
    rc, out, err = _upgrade(urig, "--json")
    plan = json.loads(out)
    assert rc == 0 and plan["pending"] == [] and [s["state"] for s in plan["steps"]] == ["current"] * 4


def test_item1_midway_failure_stops_and_keeps_done_steps(urig, monkeypatch):
    """第 2 步(工位章程同步)失败: 第 1 步已落账保留, 第 3 / 4 步未执行; 修好后重跑同一条命令只做剩下的。"""
    from test_aipos_f82_workstation_intake import RepoGate

    home, gov = urig["home"], urig["gov"]
    real_call = RepoGate.call_tool

    def gate_fetch_down(self, name, args):
        if name == "lybra_distribution_fetch":
            raise RuntimeError("fixture: 门分发取件不可用")
        return real_call(self, name, args)

    monkeypatch.setattr(RepoGate, "call_tool", gate_fetch_down)
    rc, out, err = _upgrade(urig, "--confirm", "--actor", ADV)
    log = _git(home, "log", "--format=%s", "-1")
    _show(f"[①·中途失败] rc={rc}\n{out}{err}[①·git log -1] {log}")
    assert rc == 3 and out == ""
    assert re.search(r"^\[1/4\] 配置缺省补齐: 已执行 — ", err, re.M) and "落账: verdict=PASS committed=True pushed=True" in err
    assert re.search(r"^\[2/4\] 工位章程同步: 失败 — " + re.escape(AUD) + r": sync 出错: RuntimeError: fixture: 门分发取件不可用", err, re.M)
    assert re.search(r"^\[3/4\] 记录类型补齐: 未执行\(前一步失败已停\)", err, re.M)
    assert re.search(r"^\[4/4\] 决策指针补生成: 未执行\(前一步失败已停\)", err, re.M)
    assert "已停止: 第 2 步「工位章程同步」失败, 已成功步保留" in err
    assert "结果: 已执行 1 步 · 跳过 0 步 · 失败 1 步 · 未执行 2 步" in err
    assert log == f"chore(governance): 项目配置落账 upgrade {PROJECT}"
    assert json.loads((gov / "project.json").read_text(encoding="utf-8"))["charter_auto_sync"] is True, "已成功步保留"
    assert f"?? {PROJECT}/{urig['snap']}" in _git(home, "status", "--porcelain", "-uall"), "后续步未执行"

    monkeypatch.setattr(RepoGate, "call_tool", real_call)
    rc, out, err = _upgrade(urig, "--confirm", "--actor", ADV)
    _show(f"[①·修好后重跑] rc={rc}\n{out}{err}")
    assert rc == 0 and re.search(r"^\[1/4\] 配置缺省补齐: 跳过 — 已是最新", out, re.M)
    assert "结果: 已执行 3 步 · 跳过 1 步 · 失败 0 步 · 未执行 0 步" in out


def test_item1_not_applicable_and_unresolved_target(tmp_path, monkeypatch):
    """无工位 / 治理根不在 git: 相应步「不适用」; 项目解析不出 = 拒(零写入)。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "AIPOS_WORKSPACE_ROOT", "LYBRA_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    rc, out, err = _cli("project", "new", "f176-bare", "--home-root", hroot, "--actor", OWNER)
    assert rc == 0, out + err
    rc, out, err = _cli("project", "upgrade", "f176-bare", "--home-root", hroot)
    _show(f"[①·非 git 治理根 / 无工位] rc={rc}\n{out}{err}")
    assert rc == 0
    assert re.search(r"^\[2/4\] 工位章程同步: 不适用 — ", out, re.M) and "本项目无已接入的本机工位" in out
    assert re.search(r"^\[3/4\] 记录类型补齐: 不适用 — ", out, re.M) and "不在 git 工作树内" in out
    assert "(本项目无已接入顾问实例)" in out and "顾问实例 <你的顾问实例>:" in _cli("project", "upgrade", "f176-bare", "--home-root", hroot, "--guidance")[1]
    rc, out, err = _cli("project", "upgrade", "no-such-project", "--home-root", hroot)
    _show(f"[①·项目解析不出] rc={rc}\n{out}{err}")
    assert rc == 1 and "未改动" in err


# ===========================================================================
# ③ 引导: 逐顾问话术 / 做到后消失 / --guidance CLI
# ===========================================================================

def test_item3_guidance_per_advisor_then_disappears_when_done(urig):
    home, gov = urig["home"], urig["gov"]
    before = _snapshot(home)
    rc, out, err = _upgrade(urig, "--guidance")
    _show(f"[③·--guidance] rc={rc}\n{out}{err}")
    assert rc == 0 and _snapshot(home) == before, "引导只读"
    assert f"需顾问主动用的新能力 — 项目 {PROJECT}" in out
    for inst in (ADV, ADV2):
        assert f"顾问实例 {inst}:" in out
        assert f"话术: 告诉 {inst}: {NOTICE_ACTION}" in out
        assert f"lybra updates --ack --actor {inst} --confirm" in out
    assert out.count("[onboarding] 项目还没有路线图") == 2 and out.count("[onboarding] 未声明 pi 执行 / 审计用哪个模型") == 2
    assert "lybra project set-model f176-legacy --role auditor --model <provider/id>" in out
    assert out.count("[card_field] 近期卡都没声明审计运行证据") == 2 and out.count("[card_field] 近期卡都没声明验收产物") == 2
    assert "audit_runtime_evidence: {required: true, commands: [" in out

    # 做到: ADV 读完更新并记已读; 建路线图; 新卡声明运行证据与产物
    rc, o2, e2 = _cli("updates", "--ack", "--workspace-root", gov, "--actor", ADV, "--confirm")
    assert rc == 0, o2 + e2
    rc, o3, e3 = _cli("roadmap", "add-milestone", PROJECT, "--home-root", home, "--id", "M1", "--lane", "core", "--title", "升级靶场",
                      "--actor", ADV, "--confirm")
    assert rc == 0, o3 + e3
    _write(gov / "5_tasks" / "queue" / "pending" / "f176-c3.md",
           "---\ntask_id: F176-C3\ntask_mode: code\ncreated_at: '2026-10-03T00:00:00Z'\n"
           "audit_runtime_evidence:\n  required: true\n  commands: [bash tests/run-all.sh]\n"
           "artifacts:\n- criterion: M1-C1\n  path_glob: '*.png'\n  kind: screenshot\n---\n# c3\n")
    rc, out2, err2 = _upgrade(urig, "--guidance")
    _show(f"[③·做到后 --guidance] rc={rc}\n{out2}{err2}")
    assert rc == 0
    adv_block = out2.split(f"顾问实例 {ADV}:")[1].split("顾问实例 ")[0]
    adv2_block = out2.split(f"顾问实例 {ADV2}:")[1]
    assert NOTICE_ACTION not in adv_block and NOTICE_ACTION in adv2_block, "ADV 已读即消失, ADV2 未读仍在"
    assert "项目还没有路线图" not in out2 and "近期卡都没声明" not in out2
    assert "未声明 pi 执行 / 审计用哪个模型" in out2, "未做到的仍在"
    rc, out3, _ = _upgrade(urig, "--guidance", "--json")
    data = json.loads(out3)
    _show(f"[③·--guidance --json 摘要] count={data['count']} groups={[(g['advisor'], [i['id'] for i in g['items']]) for g in data['groups']]}")
    assert rc == 0 and {g["advisor"] for g in data["groups"]} == {ADV, ADV2}


# ===========================================================================
# ② 看板: 升级页 + 写例外②(F172 同一安全框架)
# ===========================================================================

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def board(urig):
    from web.board.app import SESSION_COOKIE_NAME, SessionStore, make_handler
    from web.board.auth_otc import PreviewTokenStore

    store = SessionStore()
    owner_sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="device_code")
    owner2_sid = store.create(role="owner", scopes=["owner_confirm"], token_ref="owner-fixture", login_method="otc")
    advisor_sid = store.create(role="advisor", scopes=["queue_publish"], token_ref="advisor-fixture", login_method="token")
    previews = PreviewTokenStore(ttl_seconds=300)
    serve_root = urig["tmp"] / "serve-root"
    (serve_root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    audit_log = urig["tmp"] / "board-runtime" / "board-audit-log.jsonl"
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), make_handler(
        repo_root=serve_root, home_root=urig["home"], session_store=store, preview_store=previews,
        mint_connection_json=urig["conn"], board_audit_log_path=audit_log))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {**urig, "base": f"http://127.0.0.1:{server.server_address[1]}", "previews": previews, "audit_log": audit_log,
               "owner": f"{SESSION_COOKIE_NAME}={owner_sid}", "owner2": f"{SESSION_COOKIE_NAME}={owner2_sid}",
               "advisor": f"{SESSION_COOKIE_NAME}={advisor_sid}", "server": server}
    finally:
        server.shutdown()
        server.server_close()


def _request(b, path: str, method: str = "GET", body: dict | None = None, who: str | None = "owner",
             headers: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
    req = urllib.request.Request(b["base"] + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    if who:
        req.add_header("Cookie", b[who])
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _post(b, body: dict, who: str = "owner", headers: dict | None = None) -> tuple[int, dict]:
    status, text = _request(b, "/api/owner/projects/upgrade", "POST", body, who=who, headers=headers)
    return status, json.loads(text)


def test_item2_board_page_badge_preview_confirm(board):
    home = board["home"]
    status, page = _request(board, f"/owner/upgrade?project={PROJECT}")
    _show(f"[②·升级页 Owner GET] {status}\n{page[:3000]}")
    assert status == 200 and 'data-owner="true"' in page and 'class="upgrade-preview-button"' in page
    # 章程步取记录真相: 无判定记录 = 待现核, 不计数
    assert re.search(r'<h2 data-pending="3" data-guidance="\d+">', page)
    assert "[2/4] 工位章程同步: 待现核" in html.unescape(page)
    assert html.escape(f"告诉 {ADV}: {NOTICE_ACTION}") in page
    status, ro = _request(board, f"/owner/upgrade?project={PROJECT}", who="advisor")
    assert status == 200 and 'data-owner="false"' in ro and "upgrade-preview-button" not in ro and "只读视图" in ro
    status, overview = _request(board, "/owner/overview")
    badge = re.search(r'<li class="rp-upgrade[^"]*" data-upgrade-pending="(\d+)"><a href="([^"]+)">([^<]+)</a></li>', overview)
    _show(f"[②·总览角标] {badge.group(0) if badge else overview[:2000]}")
    assert status == 200 and badge and badge.group(1) == "3" and badge.group(2) == f"/owner/upgrade?project={PROJECT}"
    assert badge.group(3) == "可升级(3 项)"
    status, onboarding = _request(board, "/owner/onboarding")
    assert status == 200 and f'href="/owner/upgrade?project={PROJECT}"' in onboarding

    before = _snapshot(home)
    status, pre = _post(board, {"workspace": PROJECT, "stage": "preview"})
    _show(f"[②·Owner 预演] {status}\n" + "\n".join(pre.get("preview_lines") or [json.dumps(pre, ensure_ascii=False)]))
    assert status == 200 and pre["ok"] and pre["preview_token"] and pre["params"]["pending"] == [
        "config_defaults", "workstation_charter", "record_type", "decision_log_pointers"]
    assert any("[2/4] 工位章程同步: 待升级" in ln for ln in pre["preview_lines"])
    assert _snapshot(home) == before, "预演零写入"
    status, done = _post(board, {"workspace": PROJECT, "stage": "confirm", "preview_token": pre["preview_token"]})
    _show(f"[②·Owner 确认] {status}\n" + "\n".join(done.get("result_lines") or [json.dumps(done, ensure_ascii=False)]))
    assert status == 200 and done["ok"] and done["capture_surface"] == "board"
    assert done["actor"].startswith("board:owner:owner-fixture:session-sha256:")
    assert "结果: 已执行 4 步 · 跳过 0 步 · 失败 0 步 · 未执行 0 步" in done["result_lines"]
    msg = _git(home, "log", "-1", "--format=%B")
    _show(f"[②·git log -1]\n{msg}")
    assert f"Actor: {done['actor']}" in msg
    audit = [json.loads(ln) for ln in board["audit_log"].read_text(encoding="utf-8").splitlines()]
    _show(f"[②·看板审计日志] {[(a['action'], a['stage'], a['outcome'], a.get('capture_surface')) for a in audit]}")
    assert [(a["action"], a["outcome"]) for a in audit] == [("project_upgrade", "previewed"), ("project_upgrade", "confirming"),
                                                           ("project_upgrade", "upgraded")]
    assert audit[-1]["actor"] == done["actor"] and audit[-1]["capture_surface"] == "board" and audit[-1]["role"] == "owner"
    status, page2 = _request(board, f"/owner/upgrade?project={PROJECT}")
    assert re.search(r'<h2 data-pending="0" ', page2), "升级后角标归零"


def test_item2_non_owner_workspace_and_six_token_refusals(board):
    home = board["home"]
    before = _snapshot(home)
    lines = []
    status, resp = _post(board, {"workspace": PROJECT, "stage": "preview"}, who="advisor")
    lines.append(f"非 Owner 会话 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "OWNER_ONLY"
    status, resp = _post(board, {"stage": "preview"})
    lines.append(f"缺 workspace → {status} {resp['error']} {resp['message']}")
    assert status == 400 and resp["error"] == "WORKSPACE_REQUIRED"
    for bad in ("no-such-project", "../" + PROJECT, str(board["gov"])):
        status, resp = _post(board, {"workspace": bad, "stage": "preview"})
        lines.append(f"workspace={bad!r} → {status} {resp['error']} {resp['message']}")
        assert status == 400 and resp["error"] == "WORKSPACE_NOT_IN_HOME"
    status, resp = _post(board, {"workspace": PROJECT, "stage": "preview"}, headers={"Origin": "http://evil.example"})
    lines.append(f"跨站 Origin → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "ORIGIN_MISMATCH"
    _show("[②·拒绝原文]\n" + "\n".join(lines))

    lines = []

    def confirm(token, who="owner"):
        body = {"workspace": PROJECT, "stage": "confirm"}
        if token is not None:
            body["preview_token"] = token
        return _post(board, body, who=who)

    status, resp = confirm(None)
    lines.append(f"① 无令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_REQUIRED"
    status, resp = confirm("not-a-real-token")
    lines.append(f"② 错令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, other = _post(board, {"workspace": PROJECT, "stage": "preview"}, who="owner2")
    status, resp = confirm(other["preview_token"])
    lines.append(f"③ 他会话令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_SESSION_MISMATCH"
    # ④ 预演后待升级步骤变了(工位章程已被别人 sync)= 参数摘要不符
    _s, pre = _post(board, {"workspace": PROJECT, "stage": "preview"})
    from tools.aipos_cli import distribution_sync as ds

    assert ds.sync_workstation_charter(board["ws_audit"], board["gov"])["ok"]
    status, resp = confirm(pre["preview_token"])
    lines.append(f"④ 预演后待升级步骤已变 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_PARAMS_MISMATCH"
    status, resp = confirm(pre["preview_token"])
    lines.append(f"⑤ 同一令牌再用(单次即焚) → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_INVALID"
    _s, pre = _post(board, {"workspace": PROJECT, "stage": "preview"})
    store = board["previews"]
    real_now = store._now
    store._now = lambda: real_now() + 301
    try:
        status, resp = confirm(pre["preview_token"])
    finally:
        store._now = real_now
    lines.append(f"⑥ 过期令牌 → {status} {resp['error']} {resp['message']}")
    assert status == 403 and resp["error"] == "PREVIEW_TOKEN_EXPIRED"
    _show("[②·令牌校验六类拒原文]\n" + "\n".join(lines))
    assert _git(home, "rev-parse", "HEAD") == before[2], "被拒请求不落账"
    outcomes = [json.loads(ln)["outcome"] for ln in board["audit_log"].read_text(encoding="utf-8").splitlines()]
    _show(f"[②·审计日志 outcome 序列] {outcomes}")
    assert "upgraded" not in outcomes and "confirming" not in outcomes and outcomes.count("PREVIEW_TOKEN_INVALID") == 2


def test_item2_other_write_routes_still_405(board):
    before = _snapshot(board["home"])
    lines = []
    for route in STILL_READONLY_POSTS:
        status, text = _request(board, route, "POST", {"workspace": PROJECT, "stage": "confirm"})
        lines.append(f"POST {route} -> {status} {json.loads(text)['error']}")
        assert status == 405 and json.loads(text)["error"] == "BOARD_READ_ONLY", route
    for method in ("PUT", "PATCH", "DELETE"):
        status, text = _request(board, "/api/owner/projects/upgrade", method, {"stage": "confirm"})
        lines.append(f"{method} /api/owner/projects/upgrade -> {status} {json.loads(text)['error']}")
        assert status == 405
    _show("[②·其余写路由仍 405(测试客户端)]\n" + "\n".join(lines))
    assert _snapshot(board["home"]) == before


# ===========================================================================
# 防碎片化 / 声明
# ===========================================================================

def test_single_implementations_and_declarations():
    """步骤清单读声明且各步函数 = 既有命令函数; 看板只多一条写路由且经 F172 同一框架; 新配置键只进既有 schema。"""
    from tools.aipos_cli import project_upgrade as pu
    from tools.aipos_cli.board_pages import ONBOARDING_CHECKS
    from tools.aipos_cli.workspace_config import upgrade_fill_defaults
    from web.board.app import board_readonly_declaration

    decl = pu.upgrade_declaration()
    steps = {s["name"]: (s["preview"], s["execute"], s["landing"]) for s in decl["upgrade_steps"]}
    _show(f"[防碎片化·步骤声明] {json.dumps(steps, ensure_ascii=False)}")
    assert steps == {
        "config_defaults": ("tools.aipos_cli.workspace_config.fill_project_json_defaults",) * 2 + ("project_writers",),
        "workstation_charter": ("tools.aipos_cli.distribution_sync.charter_freshness",
                                "tools.aipos_cli.distribution_sync.sync_workstation_charter", None),
        "record_type": ("tools.aipos_cli.record_type_migration.run_record_type_migration",) * 2 + ("migrate_record_type",),
        "decision_log_pointers": ("tools.aipos_cli.decision_log_pointers.run_pointer_backfill",) * 2 + ("migrate_decision_log_pointers",)}
    assert set(decl["guidance"]["onboarding_items"]) <= set(ONBOARDING_CHECKS)
    assert upgrade_fill_defaults() == {"charter_auto_sync": True, "launch_resume_max": 2}
    src = (REPO_ROOT / "tools" / "aipos_cli" / "project_upgrade.py").read_text(encoding="utf-8")
    for banned in ("git\", \"commit", "write_text(", "load_role_token(", "GateClient(", "subprocess.run("):
        assert banned not in src, banned
    assert "handle_owner_write(" in src and "land_written_paths(" in src
    board_src = (REPO_ROOT / "web" / "board" / "app.py").read_text(encoding="utf-8")
    do_post = board_src[board_src.index("def do_POST(self)"):board_src.index("def do_PUT(self)")]
    # 鉴权通道 + 写例外①(信封)②(项目升级)
    assert do_post.count("if path == ") == len(board_readonly_declaration()["allowed_post_paths"]) + 2
    assert 'if path == self._upgrade_paths["write_path"]' in do_post
    env_src = (REPO_ROOT / "tools" / "aipos_cli" / "envelope_board.py").read_text(encoding="utf-8")
    assert env_src.count("preview_store.take(") == 1 and "return handle_owner_write(" in env_src
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    assert "upgrade" in verbs["two_phase_protocol"]["project_upgrade_writers"]["commands"]
    assert "upgrade" in verbs["verbs"]["lybra_governance_commit"]["post_write_landing"]["commands"]["project_writers"]["subcommands"]


def test_new_board_sources_pass_cjk_guard():
    import importlib.util

    spec = importlib.util.spec_from_file_location("f176_cjk_guard", REPO_ROOT / "web" / "board" / "tests" / "test_aipos288_cjk_source_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    hits = {name: guard.scan_file_for_cjk(REPO_ROOT / "web" / "board" / name) for name in ("static/upgrade.js", "readonly_pages.py")}
    _show(f"[CJK 守卫逐行判定] {hits}")
    assert all(v == [] for v in hits.values())
