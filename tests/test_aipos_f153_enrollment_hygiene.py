"""AIPOS-F153 — 接入卫生收尾(gap #108 / #93 / #126)。

靶场全部临时目录(临时 home 根下自造两个已建项目 proj-a 签发门 / proj-b 所属项目, 自造「人肉期」存量事件);
不碰真实治理根、真实工位、真实门与真实 connection.json。

 ① enroll-void 作用域(gap #108): 同一实例的事件分在两份日志(存量在签发方 log, 重接入后的新事件在所属项目 log)。
    缺省(不给 --log / --project)= 拒并列出两份日志(零写入); --log 只作废指定日志; --project / --code-id / --before 限定;
    两阶段 = F125 同一包装(缺省预演列出将作废的行号与所在日志, --confirm 才追加)。并入 enrollment.void_instance_events。
 ② 同机签码门地址(gap #93): 码内门地址缺省按 config.schema same_host_rule 取 loopback(端口取声明地址的端口), 只有在所属项目
    project.json workstations 声明为跨机的实例才用对外地址; 跨机而门只声明了回环 = 拒且不建码记录。guide 的码内门地址同一规则。
    并入 enrollment.resolve_gate_url_default(签码侧唯一推导)与 enroll_client.loopback_gate_url(loopback 形唯一渲染)。
 ③ 测试不写检出目录(gap #126): tools/mcp_server/tests/test_aipos296_http11_upgrade.py 的看板 handler 改用临时根;
    不变量: 测试文件不得把产品仓根交给会落 <根>/.lybra/ 的看板入口(make_handler / load_or_create_remember_secret);
    跑该文件期间对检出目录的写打开 = 0(审计钩子逐次记录, 不受该文件既存与否影响), 前后 git status(含 ignored)不变。
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f121_enroll_void import _cli, _enroll, _log, two  # noqa: E402,F401  — 靶场唯一来源(禁第二份)
from test_aipos_f149_test_audit_hygiene import _checkout_status, _test_files  # noqa: E402  — 检出目录快照 / 测试文件发现同一来源
from tools.aipos_cli import enrollment  # noqa: E402

THIS = "tests/test_aipos_f153_enrollment_hygiene.py"
INST = "hbj-auditor.proj-b.hostx"


def _show(text: str) -> None:
    print(text, flush=True)


def _bytes(*paths: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() if p.is_file() else b"<missing>" for p in paths}


def _two_logs(two) -> tuple[Path, Path, str]:
    """人肉期样本: 存量事件写在签发方 proj-a 的 log(F107 前写侧, 无 project= 字段), 重接入后的新事件在所属项目 proj-b 的 log。"""
    legacy = _log(two.a)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# Enrollment Codes Log (append-only)\n\n"
                      f"- 2026-08-28T14:50:20Z  create  code_id=enroll_legacy1  role=hbj-auditor  instance={INST}  by=x  reason=t\n"
                      f"- 2026-08-28T14:50:26Z  land  code_id=enroll_legacy1  role=hbj-auditor  instance={INST}  by=(agent-enroll)  "
                      f"reason=workstation={two.ws} files=['connection.json', 'role']\n"
                      f"- 2026-09-01T09:00:00Z  create  code_id=enroll_legacy2  role=hbj-auditor  instance={INST}  by=x  reason=t\n",
                      encoding="utf-8")
    new_code = _enroll(two, INST, two.ws)
    return legacy, _log(two.b), new_code


def _void(two, *extra: str) -> subprocess.CompletedProcess:
    return _cli(two, "enroll-void", "--instance", INST, "--reason", "人肉期存量作废(F153 夹具)", "--actor", "advisor.fixture", *extra)


# ===========================================================================
# ① enroll-void 作用域
# ===========================================================================

def test_item1_default_scope_refused_lists_logs(two):
    legacy, owned, _code = _two_logs(two)
    before = _bytes(legacy, owned)
    bare = _void(two, "--confirm")
    _show(f"---- ① 缺省作用域(--confirm 但不给 --log/--project)原文 exit={bare.returncode} ----\n{bare.stdout}{bare.stderr}")
    assert bare.returncode != 0 and "须显式作用域" in bare.stderr
    assert str(legacy) in bare.stderr and str(owned) in bare.stderr  # 出口: 两份日志都列出
    both = _void(two, "--log", str(legacy), "--project", "proj-b")
    stray = _void(two, "--log", str(two.tmp / "elsewhere.md"))
    _show(f"[① 两个作用域都给] exit={both.returncode} {both.stderr.strip()}\n[① 日志不在扫描范围] exit={stray.returncode} {stray.stderr.strip()}")
    assert both.returncode != 0 and "只能给一个" in both.stderr
    assert stray.returncode != 0 and "不是含实例" in stray.stderr
    assert _bytes(legacy, owned) == before
    assert enrollment.workstation_location(two.b, INST)["found"] is True


def test_item1_log_scope_voids_only_that_log(two):
    legacy, owned, _code = _two_logs(two)
    before = _bytes(legacy, owned)
    dry = _void(two, "--log", str(legacy))
    _show(f"---- ① --log <签发方 log> 缺省预演原文 ----\n{dry.stdout}{dry.stderr}")
    assert dry.returncode == 0, dry.stderr
    assert _bytes(legacy, owned) == before  # 预演零写入
    assert "预览(未写" in dry.stdout and f"log: {legacy}" in dry.stdout
    for n in (3, 4, 5):
        assert f"行 {n}:" in dry.stdout
    assert str(owned) not in dry.stdout

    run = _void(two, "--log", str(legacy), "--confirm", "--json")
    assert run.returncode == 0, run.stderr
    rep = json.loads(run.stdout)
    _show(f"---- ① --log --confirm --json 原文 ----\n{run.stdout}")
    legacy_after = legacy.read_text(encoding="utf-8")
    _show(f"---- ① 签发方 log 执行后原文 ----\n{legacy_after}")
    assert legacy_after.startswith(before[str(legacy)].decode("utf-8"))
    assert legacy_after.count("\n") == before[str(legacy)].decode("utf-8").count("\n") + 1
    assert rep["entries"][0]["voids_lines"] == "3-5" and rep["scope"]["log"] == str(legacy)
    assert _bytes(owned) == {str(owned): before[str(owned)]}  # 所属项目 log 逐字节不变
    loc = enrollment.workstation_location(two.b, INST)
    _show(f"[①] 作废后所属项目 proj-b 的工位定位(新 land 保留) = found={loc['found']} dir={loc['dir']}")
    assert loc["found"] is True
    where = enrollment.enrollment_whereabouts(two.a, INST)
    by_log = {item["log"]: item for item in where["found"]}
    assert by_log[str(legacy)]["voided_events"] == 3 and by_log[str(legacy)]["actions"] == {}
    assert by_log[str(owned)]["voided_events"] == 0 and by_log[str(owned)]["actions"] == {"create": 1, "use": 1, "land": 1}


def test_item1_project_code_id_and_before_narrow(two):
    legacy, owned, new_code = _two_logs(two)
    before = _bytes(legacy, owned)
    # --code-id 只作废该码的事件(签发方 log 里 legacy2 只有 1 行)
    rep = enrollment.void_instance_events(two.a, INST, by="advisor.fixture", reason="r", dry_run=True,
                                          log=str(legacy), code_ids=["enroll_legacy2"])
    assert [e["line"] for e in rep["entries"][0]["events"]] == [5] and rep["entries"][0]["left_live"] == 2
    # --before 只作废早于该时刻的事件
    rep = enrollment.void_instance_events(two.a, INST, by="advisor.fixture", reason="r", dry_run=True,
                                          log=str(legacy), before="2026-08-30T00:00:00Z")
    assert [e["line"] for e in rep["entries"][0]["events"]] == [3, 4]
    # --project 选所属项目 log; 未知 code_id / 坏 --before / 范围内无事件 = 拒
    rep = enrollment.void_instance_events(two.a, INST, by="advisor.fixture", reason="r", dry_run=True, project="proj-b",
                                          code_ids=[new_code])
    assert rep["entries"][0]["log"] == str(owned) and rep["entries"][0]["event_count"] == 3
    with pytest.raises(ValueError, match="code-id"):
        enrollment.void_instance_events(two.a, INST, by="x", reason="r", dry_run=False, project="proj-b", code_ids=["enroll_nope"])
    with pytest.raises(ValueError, match="ISO"):
        enrollment.void_instance_events(two.a, INST, by="x", reason="r", dry_run=False, project="proj-b", before="昨天")
    with pytest.raises(ValueError, match="无范围内未作废事件"):
        enrollment.void_instance_events(two.a, INST, by="x", reason="r", dry_run=False, project="proj-b", before="2000-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="无实例"):
        enrollment.void_instance_events(two.a, INST, by="x", reason="r", dry_run=False, project="proj-zzz")
    assert _bytes(legacy, owned) == before


def test_item1_two_phase_reuses_f125_wrapper():
    """两阶段旗标经 F125 同一包装注册(族 enrollment_log_writers), 状态文案读声明; 无第二套旗标注册 / 无第二个作废实现。"""
    from tools.aipos_cli.aipos_cli import _two_phase_family_declaration, build_parser
    from tools.schema_loader import load_schema

    decl = load_schema("verbs")["two_phase_protocol"]
    fam = _two_phase_family_declaration("enrollment_log_writers")
    assert fam["commands"] == ["enroll-void"] and fam["flags"] == decl["project_json_writers"]["flags"]
    assert fam["outcome_labels"] == decl["project_json_writers"]["outcome_labels"] and "flags" not in decl["enrollment_log_writers"]
    args = build_parser().parse_args(["roles", "enroll-void", "--instance", "i", "--reason", "r", "--actor", "a", "--log", "/x"])
    assert args.confirm is False and args.dry_run is False and args.void_log == "/x"  # 缺省 = 预演
    src = (REPO_ROOT / "tools" / "aipos_cli" / "aipos_cli.py").read_text(encoding="utf-8")
    seg = src[src.index('add_parser("enroll-void"'):src.index('add_parser("enroll-void"') + 3000]
    assert '"--dry-run", action="store_true"' not in seg and '_project_json_two_phase_flags(roles_enroll_void_parser, "enroll-void", family="enrollment_log_writers")' in seg
    en = (REPO_ROOT / "tools" / "aipos_cli" / "enrollment.py").read_text(encoding="utf-8")
    assert en.count("def void_instance_events(") == 1 and en.count("VOID_ACTION, code_id=") == 1


# ===========================================================================
# ② 同机签码门地址 loopback
# ===========================================================================

def _gate_conn(root: Path, rpc_url: str) -> Path:
    conn = root / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(conn.read_text(encoding="utf-8")) if conn.is_file() else {"config_version": 1, "tokens": []}
    data["mcp"] = {"rpc_url": rpc_url}
    conn.write_text(json.dumps(data), encoding="utf-8")
    return conn


def test_item2_same_host_code_embeds_loopback_cross_host_declared_external(two):
    from tools.aipos_cli.workspace_config import set_project_workstation

    _gate_conn(two.a, "http://gate-host.example.ts.net:7121/mcp")  # 门工作区声明的对外地址(夹具域名, 非真实)
    same = enrollment.issue_self_contained_code(two.a, role="executor", instance="exec.proj-b.hostx", ttl_seconds=600,
                                                governance_root=str(two.b), by="fixture", reason="同机")
    decoded = enrollment.decode_self_contained_code(same["self_contained_code"])
    _show(f"[② 同机签码] gate_url={same['gate_url']}  码内 gate_url={decoded['gate_url']}\n            来源: {same['gate_url_source']}")
    assert same["gate_url"] == decoded["gate_url"] == "http://127.0.0.1:7121"
    assert "same_host_rule" in same["gate_url_source"]

    set_project_workstation(two.b, "exec.proj-b.macx", gate_ssh_alias="dev", material_access="经 ssh dev 读写治理根", dry_run=False)
    cross = enrollment.issue_self_contained_code(two.a, role="executor", instance="exec.proj-b.macx", ttl_seconds=600,
                                                 governance_root=str(two.b), by="fixture", reason="跨机")
    _show(f"[② 声明跨机签码] gate_url={cross['gate_url']}\n            来源: {cross['gate_url_source']}")
    assert cross["gate_url"] == "http://gate-host.example.ts.net:7121" and "workstations.exec.proj-b.macx" in cross["gate_url_source"]

    explicit = enrollment.issue_self_contained_code(two.a, role="executor", instance="exec.proj-b.hostx", ttl_seconds=600,
                                                    gate_url="http://explicit.example:7122", governance_root=str(two.b), by="f")
    assert explicit["gate_url"] == "http://explicit.example:7122" and explicit["gate_url_source"] == "显式 gate_url"

    # 跨机而门只声明了回环 = 拒, 且不建码记录、不铸运输凭证
    _gate_conn(two.a, "http://127.0.0.1:7121/mcp")
    store = _bytes(two.a / ".lybra" / "enrollments.json", two.a / ".lybra" / "connection.json")
    with pytest.raises(ValueError, match="跨机工位") as exc:
        enrollment.issue_self_contained_code(two.a, role="executor", instance="exec.proj-b.macx", ttl_seconds=600,
                                             governance_root=str(two.b), by="fixture", reason="跨机")
    _show(f"[② 跨机但只有回环地址] 拒: {exc.value}")
    assert _bytes(two.a / ".lybra" / "enrollments.json", two.a / ".lybra" / "connection.json") == store


def test_item2_guide_gate_address_same_rule(two):
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    conn = _gate_conn(two.tmp / "owner-ws", "http://gate-host.example.ts.net:7121/mcp")
    guide = generate_onboarding_guide("probe153", home_root=str(two.home), owner_connection_json=str(conn))
    _show(f"[② guide] 门地址: {guide['gate_url']}(来源: {guide['gate_url_source']})")
    assert guide["gate_url"] == "http://127.0.0.1:7121" and "same_host_rule" in guide["gate_url_source"]
    commands = [c for step in guide["steps"] for c in (step.get("commands") or [step.get("command") or ""])]
    code_cmds = [c for c in commands if "enroll-code" in c]
    assert code_cmds and all("--gate-url http://127.0.0.1:7121" in c for c in code_cmds), code_cmds
    assert "ts.net" not in json.dumps(guide["steps"], ensure_ascii=False)
    explicit = generate_onboarding_guide("probe153", home_root=str(two.home), owner_connection_json=str(conn),
                                         gate_url="http://explicit.example:7122")
    assert explicit["gate_url"] == "http://explicit.example:7122" and explicit["gate_url_source"] == "显式 --gate-url"


def test_item2_single_loopback_renderer():
    """loopback 形只在 enroll_client.loopback_gate_url 渲染; 工位侧规范化与签码侧 / guide 都调它。"""
    from tools.aipos_cli import enroll_client, onboarding

    import inspect

    src = inspect.getsource(enroll_client)
    assert src.count('f"http://127.0.0.1:{port}"') == 1 and "return loopback_gate_url(gate_url)" in src
    assert "loopback_gate_url(" in inspect.getsource(enrollment.resolve_gate_url_default)
    assert "loopback_gate_url(" in inspect.getsource(onboarding._gate_url)
    for fn in (enrollment.resolve_gate_url_default, enroll_client.loopback_gate_url, onboarding._gate_url,
               enrollment.void_instance_events):
        body = inspect.getsource(fn)
        assert "except Exception" not in body and "pass\n" not in body, fn.__name__


# ===========================================================================
# ③ 测试不写检出目录(覆盖 tools/mcp_server/tests)
# ===========================================================================
_ROOT_NAME_RE = re.compile(r"^[A-Z_]*(?:REPO|PRODUCT)_ROOT$")
_BOARD_ROOT_SINKS = {"make_handler": "repo_root", "load_or_create_remember_secret": "repo_root"}

_WRITE_PROBE = textwrap.dedent('''
    import json, os, sys
    root = os.path.realpath(sys.argv[1])
    writes = []

    def _hit(path):
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not isinstance(path, str):
            return
        full = os.path.realpath(path)
        if (full == root or full.startswith(root + os.sep)) and "__pycache__" not in full and ".pytest_cache" not in full:
            writes.append(full)

    # 带 dir_fd 的相对路径(如 TemporaryDirectory 清理时 shutil.rmtree 逐项 os.remove(名, dir_fd=…))不相对 cwd 解析, 跳过;
    # os.open 的 open 事件不带 dir_fd(mode=None), 其相对路径同理跳过; 绝对路径一律判定。
    def hook(event, args):
        if event == "open":
            path, mode, flags = args
            if mode is None and isinstance(path, (str, bytes)) and not os.path.isabs(path):
                return
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)):
                _hit(path)
        elif event in ("os.mkdir", "os.rename", "os.remove", "os.rmdir", "os.symlink", "os.chmod"):
            if args[-1] is not None and isinstance(args[-1], int) and not os.path.isabs(os.fsdecode(args[0]) if isinstance(args[0], bytes) else str(args[0])):
                return
            _hit(args[0])

    sys.addaudithook(hook)
    import pytest
    rc = pytest.main(sys.argv[2:])
    print("CHECKOUT_WRITES=" + json.dumps(sorted(set(writes)), ensure_ascii=False))
    sys.exit(rc)
''')


def _run_with_write_probe(root: Path, *pytest_args: str, cwd: Path) -> tuple[subprocess.CompletedProcess, list[str]]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO_ROOT)}
    res = subprocess.run([sys.executable, "-c", _WRITE_PROBE, str(root), *pytest_args, "-q", "-p", "no:cacheprovider"],
                         cwd=str(cwd), env=env, capture_output=True, text=True, timeout=300)
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith("CHECKOUT_WRITES=")), None)
    assert line is not None, res.stdout[-2000:] + res.stderr[-2000:]
    return res, json.loads(line.split("=", 1)[1])


def test_item3_no_test_hands_checkout_to_board_root_sinks():
    """不变量(防回潮): 任何测试文件都不得把产品仓根(<*>REPO_ROOT / PRODUCT_ROOT)交给会落 <根>/.lybra/ 的看板入口。"""
    hits = []
    for rel in _test_files():
        if not rel.endswith(".py") or rel == THIS:
            continue
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name not in _BOARD_ROOT_SINKS:
                continue
            args = [kw.value for kw in node.keywords if kw.arg == _BOARD_ROOT_SINKS[name]] + node.args[:1]
            if any(isinstance(a, ast.Name) and _ROOT_NAME_RE.match(a.id) for a in args):
                hits.append(f"{rel}:{node.lineno}: {name}(… {ast.unparse(args[0])} …)")
    _show(f"[③] 测试文件 {len(_test_files())} 个; 产品仓根交给看板根入口的调用 {len(hits)} 处")
    assert hits == [], "测试把产品仓根交给看板根入口(会写 <根>/.lybra/remember_secret 等; 改用临时根):\n" + "\n".join(hits)


def test_item3_write_probe_detects_writes(tmp_path):
    """检测器自证: 一个往靶根写文件的探针用例 = 被记到(防检测器失效后不变量空转)。"""
    probe = tmp_path / "test_probe_writes.py"
    probe.write_text("from pathlib import Path\n\ndef test_w():\n"
                     f"    (Path({str(tmp_path)!r}) / '.lybra').mkdir()\n"
                     f"    (Path({str(tmp_path)!r}) / '.lybra' / 'remember_secret').write_text('x')\n", encoding="utf-8")
    res, writes = _run_with_write_probe(tmp_path, str(probe), cwd=tmp_path)
    assert res.returncode == 0, res.stdout[-2000:]
    assert str(tmp_path / ".lybra" / "remember_secret") in writes and str(tmp_path / ".lybra") in writes


def test_item3_mcp_server_http11_test_leaves_checkout_unchanged():
    """跑 tools/mcp_server/tests/test_aipos296_http11_upgrade.py: 对检出目录写打开 0 次, 前后 git status(含 ignored)逐行不变。"""
    target = "tools/mcp_server/tests/test_aipos296_http11_upgrade.py"
    before = _checkout_status()
    res, writes = _run_with_write_probe(REPO_ROOT, target, cwd=REPO_ROOT)
    after = _checkout_status()
    _show(f"[③] {target}: rc={res.returncode} {res.stdout.strip().splitlines()[-2] if len(res.stdout.strip().splitlines()) > 1 else ''}\n"
          f"[③] 对检出目录的写 {len(writes)} 次 {writes}; git status(含 ignored)前 {len(before)} 行 / 后 {len(after)} 行; "
          f"新增 {sorted(set(after) - set(before))}")
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-2000:]
    assert writes == []
    assert after == before
