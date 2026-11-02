"""AIPOS-F262 — 接入向导对齐现行流程(产品化 P2a)。

件① 授权: 向导第 3 步 = Owner 签项目常设授权(`lybra project grant sign`, AIPOS-F224; 预演 → --confirm), 之后各 enroll 命令的授权引用
    = 该授权 ID; 已有项目授权 / 旧信封的项目跑向导不报错且第 3 步提示(验收②); 信封面兼容不教学; `roles enroll-deliver --help` 的
    --owner-authorization-ref 说明改为常设授权语汇。
件② 执行分工成步: 审计工位(pi)→ pi 执行工位(pi)→ 子 agent 执行者(--executor-mode subagent)→ set-execution(三者齐)→ sync;
    每条 enroll 显式带 --executor-mode; 一个目录只给一个实例(同目录 = 拒); 工位模式包办的旧流程不再生成。
件③ 去本机假设: Owner 凭据位置只从门注册表的已声明来源推导(持 Owner 凭据条目的 connection.json), home 根里有名为 lybra 的项目不再被
    当成别的项目的凭据库(验收③); 推不出 = 明说并请用户给 --owner-connection-json; 工位缺省目录读 project.json paths.workstations_root
    (config.schema 缺省模板), 落在 home 根 / 治理根内 = 拒。
件④ 按需声明一节; QUICKSTART 两份「向导与本文不一致」提示块已删(验收④)。

端到端照抄验收(临时门, 验收①)见 tests/test_aipos_f92_onboarding_walkthrough.py(向导原样走到首卡结案)。
纯临时 HOME / 临时 home 根; 不连真实门、不读真实凭据。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_aipos_f73d_loop_driver import init_governance_repo  # noqa: E402  — 靶场治理仓唯一来源
from test_aipos_f85_onboarding_guide_generic import _iter_command_lines, _parse_lybra, _segments  # noqa: E402

PROJECT = "demo-f262"


def _show(text: str) -> None:
    print(text, flush=True)


def _cli(*argv: str) -> tuple[int, str]:
    from tools.aipos_cli.aipos_cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = main([str(a) for a in argv])
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 1
    return int(rc or 0), out.getvalue()


@pytest.fixture
def iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("LYBRA_HOME_ROOT", "LYBRA_ACTIVE_PROJECT", "LYBRA_GATE_URL", "LYBRA_CONNECTION_JSON", "AIPOS_WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    hroot = home / "lybra-home"
    hroot.mkdir()
    return {"home": home, "hroot": hroot, "tmp": tmp_path}


def _new_project(hroot: Path, name: str) -> Path:
    rc, out = _cli("project", "new", name, "--home-root", hroot, "--actor", "owner")
    assert rc == 0, out
    return hroot / name


def _owner_connection(ws: Path, *, role: str = "owner") -> Path:
    conn = ws / ".lybra" / "connection.json"
    conn.parent.mkdir(parents=True, exist_ok=True)
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:7999/mcp"},
                                "tokens": [{"role": role, "token": f"fixture-{role}-f262", "token_ref": f"{role}-fixture-f262",
                                            "scopes": ["owner_confirm"], "fingerprint": "sha256:fixture"}]}), encoding="utf-8")
    return conn


def _guide(hroot: Path, **kw) -> dict:
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    return generate_onboarding_guide(PROJECT, home_root=str(hroot), host_segment="h1", repos=["app=/tmp/f262-app"], **kw)


# ===========================================================================
# 件③ / 验收③: Owner 凭据推导不再按项目名猜
# ===========================================================================

def test_accept3_owner_credential_not_derived_to_lybra_named_project(iso):
    from tools.aipos_cli.service_mode import connection_path
    from tools.aipos_cli.workspace_config import resolve_active_project

    hroot = iso["hroot"]
    _new_project(hroot, "lybra")  # home 根里只有一个名为 lybra 的项目, 它不持 Owner 凭据
    before = resolve_active_project(hroot)  # 修前推导口(home 根 + 活动项目梯: 只有一个项目 = 回落到它)
    _show(f"[验收③·修前推导口] resolve_active_project(home) = {before!r} → 修前 Owner 凭据 = {connection_path(hroot / before)}")
    assert before == "lybra"
    guide = _guide(hroot)
    cred = guide["owner_credential"]
    _show(f"[验收③·修后] owner_connection_json={guide['owner_connection_json']} resolved={cred['resolved']}\n  {cred['reason']}")
    assert cred["resolved"] is False and guide["owner_connection_json"] == "<OWNER_CONNECTION_JSON>"
    assert "/lybra/.lybra" not in json.dumps(guide, ensure_ascii=False)
    assert "--owner-connection-json" in cred["reason"]  # 明说并请用户给出, 不写推导值冒充
    from tools.aipos_cli.onboarding import STEP, format_guide_text

    text = format_guide_text(guide)
    assert "⚠ Owner 凭据未推导出" in text.splitlines()[5]
    grant = guide["steps"][STEP["grant"] - 1]
    assert "--connection-json <OWNER_CONNECTION_JSON>" in grant["command"] and "⚠ Owner 凭据未推导出" in grant["command"]


def test_item3_owner_credential_from_declared_registry_sources(iso, monkeypatch):
    hroot = iso["hroot"]
    _new_project(hroot, "lybra")
    gate = _new_project(hroot, "ops")
    conn = _owner_connection(gate)
    guide = _guide(hroot)
    _show(f"[件③·唯一持 Owner 凭据] {guide['owner_connection_json']}(来源: {guide['owner_credential']['source']})")
    assert guide["owner_connection_json"] == str(conn) and guide["owner_workspace"] == str(gate)
    assert "项目 ops" in guide["owner_credential"]["source"]
    # 两份都持 Owner 凭据: 未声明活动项目 = 推不出(列出两份); 声明了 = 取声明那份
    lyb_conn = _owner_connection(hroot / "lybra")
    two = _guide(hroot)["owner_credential"]
    _show(f"[件③·两份无声明] {two['reason']}")
    assert two["resolved"] is False and set(two["holders"]) == {str(conn), str(lyb_conn)}
    monkeypatch.setenv("LYBRA_ACTIVE_PROJECT", "ops")
    picked = _guide(hroot)
    assert picked["owner_connection_json"] == str(conn) and "已声明活动项目 ops" in picked["owner_credential"]["source"]
    # 非 Owner 条目不算; 显式参数优先
    monkeypatch.delenv("LYBRA_ACTIVE_PROJECT")
    _owner_connection(hroot / "lybra", role="advisor")
    assert _guide(hroot)["owner_connection_json"] == str(conn)
    explicit = _guide(hroot, owner_connection_json=str(iso["tmp"] / "vault" / ".lybra" / "connection.json"))
    assert explicit["owner_workspace"] == str(iso["tmp"] / "vault") and explicit["owner_credential"]["source"] == "显式 --owner-connection-json"


def test_item3_unreadable_registry_file_is_reported_not_swallowed(iso):
    hroot = iso["hroot"]
    gate = _new_project(hroot, "ops")
    (gate / ".lybra").mkdir()
    (gate / ".lybra" / "connection.json").write_text("{not json", encoding="utf-8")
    cred = _guide(hroot)["owner_credential"]
    _show(f"[件③·读不出] {cred['reason']}")
    assert cred["resolved"] is False and cred["unreadable"] and "读不出" in cred["reason"]


# ===========================================================================
# 件③: 工位缺省目录读声明
# ===========================================================================

def test_item3_workstation_dirs_from_declaration(iso):
    from tools.aipos_cli.onboarding import STEP

    hroot = iso["hroot"]
    guide = _guide(hroot)
    ws = guide["workstations"]
    _show(f"[件③·缺省工位目录] {ws} 来源: {guide['workstations_source']}")
    assert ws["pi_executor"] == f"~/lybra-workstations/{PROJECT}/executor" and ws["auditor"] == f"~/lybra-workstations/{PROJECT}/auditor"
    assert "config.schema 缺省 project_json.paths.workstations_root" in guide["workstations_source"]
    assert f"--workspace ~/lybra-workstations/{PROJECT}/auditor" in guide["steps"][STEP["auditor"] - 1]["command"]
    # 项目声明后向导用声明值(写入口 = set-paths 两阶段; 已接入工位不迁移, 只影响向导给新接入的目录)
    gov = _new_project(hroot, PROJECT)
    init_governance_repo(hroot)
    declared = iso["tmp"] / "stations" / PROJECT
    rc, out = _cli("project", "set-paths", PROJECT, "--home-root", hroot, "--key", "workstations_root", "--value", declared, "--confirm")
    assert rc == 0, out
    guide = _guide(hroot)
    assert guide["workstations"]["pi_executor"] == f"{declared}/executor" and "项目声明" in guide["workstations_source"]
    assert json.loads((gov / "project.json").read_text(encoding="utf-8"))["paths"]["workstations_root"] == str(declared)
    # 声明落在 home 根内(治理仓会收进工位凭据)= 拒并给出口
    rc, out = _cli("project", "set-paths", PROJECT, "--home-root", hroot, "--key", "workstations_root", "--value", hroot / "stations",
                   "--confirm")
    assert rc == 0, out
    with pytest.raises(ValueError) as exc:
        _guide(hroot)
    _show(f"[件③·工位根落 home 根内 拒] {exc.value}")
    assert "home 根" in str(exc.value) and "--workspace-dir" in str(exc.value)
    assert _guide(hroot, workspace_dir=str(iso["tmp"] / "e"), auditor_dir=str(iso["tmp"] / "a"))["workstations_source"].startswith("显式")


def test_item2_same_directory_for_two_instances_refused(iso):
    with pytest.raises(ValueError) as exc:
        _guide(iso["hroot"], workspace_dir=str(iso["tmp"] / "ws"), auditor_dir=str(iso["tmp"] / "ws"))
    _show(f"[件②·同目录两实例 拒] {exc.value}")
    assert "同一目录" in str(exc.value) and "一个目录只能登记给一个实例" in str(exc.value)
    with pytest.raises(ValueError):
        _guide(iso["hroot"], workspace_dir=str(iso["hroot"] / PROJECT), auditor_dir=str(iso["tmp"] / "a"))  # 撞治理根(子 agent 凭据落点)


# ===========================================================================
# 件① / 验收②: 授权
# ===========================================================================

def test_item1_grant_step_and_auth_refs(iso):
    from tools.aipos_cli.onboarding import STEP

    guide = _guide(iso["hroot"])
    auth = guide["authorization"]
    _show("[件①·第 3 步]\n" + guide["steps"][STEP["grant"] - 1]["command"])
    assert auth["authorization_id"] == f"auth_{PROJECT}" and auth["lanes"] == ["app"] and auth["drivers"] == [guide["instances"]["advisor"]]
    assert sorted(auth["hosts"]) == ["h1", "h1-pi"] and auth["launch_harnesses"] == ["pi"]
    refs = []
    for _, line in _iter_command_lines(guide):
        for seg in _segments(line):
            if seg[:3] == ["lybra", "roles", "--workspace-root"] or (seg[:2] == ["lybra", "roles"] and "enroll-deliver" in seg):
                a = _parse_lybra(seg[1:], line)
                if getattr(a, "roles_command", None) == "enroll-deliver":
                    refs.append((a.instance, a.executor_mode, a.owner_authorization_ref))
    _show(f"[件①·接入命令授权引用] {refs}")
    assert len(refs) == 8 and {r[2] for r in refs} == {auth["authorization_id"]}
    assert [r[1] for r in refs[2:]] == ["workstation"] * 4 + ["subagent"] * 2  # 件②: 工位接入显式带 --executor-mode
    blob = json.dumps(guide["steps"], ensure_ascii=False)
    assert "envelope mint" not in blob and "--envelope" not in blob and "pol_" not in blob  # 信封面不教学


def test_accept2_legacy_envelopes_and_existing_grant_hint(iso, monkeypatch):
    """验收②: 已有三信封的旧项目跑向导不报错, 第 3 步提示合并为项目授权; 已签项目授权的项目提示不必重签(grant show / extend)。"""
    import test_aipos_f224_project_authorization as f224
    from tools.aipos_cli import autonomy_policy as ap
    from tools.aipos_cli.onboarding import STEP, format_guide_text

    hroot = iso["hroot"]
    gov = _new_project(hroot, PROJECT)
    for pid, agent in ((f"pol_{PROJECT}_loop_1", f"advisor.{PROJECT}.h1"), (f"pol_{PROJECT}_exec_1", f"exec.{PROJECT}.h1"),
                       (f"pol_{PROJECT}_audit_1", f"audit.{PROJECT}.h1")):
        f224._envelope(gov, pid, agent, max_tasks=5)
    guide = _guide(hroot)
    note = guide["steps"][STEP["grant"] - 1]["note"]
    _show(f"[验收②·三信封旧项目 第 3 步提示] {note}")
    assert "3 张旧信封" in note and "grant draft" in note and "--from-envelopes" in note
    assert "旧信封" in format_guide_text(guide)
    # 已签项目授权(进程内 Owner 门替身, 门侧真实处理器)= 提示不必重签
    import tools.aipos_cli.confirm_client as confirm_client

    init_governance_repo(hroot)
    (iso["tmp"] / "app").mkdir()  # 授权车道仓须在盘上(门侧解析 lane.repo)
    rc, out = _cli("project", "set-repos", PROJECT, "--home-root", hroot, "--repo", f"app={iso['tmp'] / 'app'}", "--confirm")
    assert rc == 0, out
    from tools.aipos_cli.onboarding import generate_onboarding_guide

    # 已建项目不给 --repo: 授权车道取已声明仓清单(不写占位)
    assert generate_onboarding_guide(PROJECT, home_root=str(hroot), host_segment="h1")["authorization"]["lanes"] == ["app"]
    conn = iso["tmp"] / "owner-vault" / "connection.json"
    conn.parent.mkdir()
    conn.write_text(json.dumps({"mcp": {"rpc_url": "http://127.0.0.1:9/mcp"},
                                "tokens": [{"role": "owner", "token": f224.OWNER_TOKEN, "scopes": ["owner_confirm"]}]}), encoding="utf-8")
    monkeypatch.setattr(confirm_client, "GateClient", f224.OwnerGate)
    rc, out = _cli("project", "grant", "sign", PROJECT, "--home-root", hroot, "--grant-lane", "app", "--task-mode", "code",
                   "--driver", f"advisor.{PROJECT}.h1", "--host", "h1", "--max-tasks", "5", "--expires-at", "2999-01-01T00:00:00Z",
                   "--confirm", "--connection-json", conn)
    assert rc == 0, out
    assert ap.authorization_ids(gov) == [f"auth_{PROJECT}"]
    guide = _guide(hroot)
    note = guide["steps"][STEP["grant"] - 1]["note"]
    _show(f"[验收②·已有项目授权 第 3 步提示] {note}")
    assert f"已有项目授权: auth_{PROJECT} r1(状态 active" in note and "不必重签" in note and "grant extend" in note
    from tools.aipos_cli.onboarding import validate_step_prerequisites

    assert "authorization" not in validate_step_prerequisites(STEP["advisor"], project_name=PROJECT, home_root=str(hroot))["missing"]


def test_item1_check_reports_missing_grant(iso):
    from tools.aipos_cli.onboarding import STEP, validate_step_prerequisites

    hroot = iso["hroot"]
    _new_project(hroot, PROJECT)
    result = validate_step_prerequisites(STEP["advisor"], project_name=PROJECT, home_root=str(hroot))
    _show(f"[件①·onboarding check 缺授权] {result}")
    assert "authorization" in result["missing"] and any("lybra project grant sign" in g for g in result["guidance"])


def test_item1_enroll_deliver_help_uses_grant_wording():
    rc, out = _cli("roles", "enroll-deliver", "--help")
    flat = " ".join(out.split())
    _show(f"[件①·enroll-deliver --help] {flat[flat.find('--owner-authorization-ref OWNER'):][:400]}")
    assert "project standing grant id" in flat and "lybra project grant show" in flat
    assert "the envelope covering that workstation)" not in flat


# ===========================================================================
# 件② / 件④: 执行分工成步、按需声明、旧流程拒
# ===========================================================================

def test_item2_division_steps_and_workstation_mode_refused(iso):
    from tools.aipos_cli.onboarding import STEP

    guide = _guide(iso["hroot"])
    titles = [s["title"] for s in guide["steps"]]
    _show("[件②·步骤]\n" + "\n".join(f"  {i}. {t}" for i, t in enumerate(titles, 1)))
    order = [STEP[k] for k in ("auditor", "pi_executor", "subagent_executor", "execution", "sync")]
    assert order == sorted(order) and guide["division"] == {"code": guide["instances"]["executor"],
                                                            "noncode": guide["instances"]["pi_executor"], "audit": guide["instances"]["auditor"]}
    first = guide["steps"][STEP["first_card"] - 1]["command"]
    assert "kickoff --task-id" in first and "/go" in first
    rc, out = _cli("onboarding", "guide", PROJECT, "--home-root", iso["hroot"], "--executor-mode", "workstation")
    _show(f"[件②·--executor-mode workstation] exit {rc}: {out.strip()}")
    assert rc == 2 and "执行分工" in out


def test_item4_on_demand_section_listed_not_forced(iso):
    from tools.aipos_cli.onboarding import format_guide_text

    guide = _guide(iso["hroot"])
    text = format_guide_text(guide)
    section = text[text.index("═══ 按需声明"):text.index("═══ 附: 接入既有人肉项目")]
    _show(section)
    for item in guide["on_demand"]:
        assert item["purpose"] and item["when"] and item["commands"]
        assert all("--confirm" not in c for c in item["commands"])
    for word in ("card-policy", "set-test-contract", "release-target", "set-model", "project headline"):
        assert word in section, word


# 英文第 4 步标题的术语位按术语单一来源 schema/config.schema.json owner_texts.glossary 取词(AIPOS-F281):
# 测试不写死英文措辞, 术语再改只改 glossary 一处。标题骨架(序号 / 动词 / 角色)仍按字面核。
GRANT_TERM_ZH = "项目常设授权"
EN_STEP4_PREFIX = "## Step 4 — Sign the "
EN_STEP4_SUFFIX = " (Owner)"


def _glossary_term(zh: str) -> dict:
    from tools.schema_loader import code_repo_schema_root, load_schema

    terms = load_schema("config", code_repo_schema_root())["owner_texts"]["glossary"]["terms"]
    found = [t for t in terms if t.get("zh") == zh]
    assert len(found) == 1 and found[0].get("en"), f"glossary 未唯一声明术语 {zh!r}: {found}"
    return found[0]


def en_step4_heading_problems(en: str, term: dict) -> list[str]:
    """英文 QUICKSTART 第 4 步标题判定(空 = 合格)。

    合格 = 恰一行 `## Step 4 — Sign the <术语> (Owner)`, 术语位 = glossary en 值。
    过渡期容忍(AIPOS-F279 合入前): 术语位用该条 avoid 词(旧译)且全文其余行仍有同一 avoid 词
    = 文档整体尚未按 glossary 迁移, 标题与正文同旧, 不单独判红; 一旦正文其余行已清掉该旧译(F279 合入即如此),
    标题仍用旧译 = 判红(自然收紧, 无需再改本测试)。
    """
    lines = en.splitlines()
    heads = [i for i, line in enumerate(lines) if line.startswith(EN_STEP4_PREFIX)]
    if len(heads) != 1:
        return [f"应恰有一行以 {EN_STEP4_PREFIX!r} 开头, 实有 {len(heads)} 行"]
    head = lines[heads[0]]
    if not head.endswith(EN_STEP4_SUFFIX):
        return [f"第 4 步标题须以 {EN_STEP4_SUFFIX!r} 结尾: {head!r}"]
    slot = head[len(EN_STEP4_PREFIX):-len(EN_STEP4_SUFFIX)]
    if slot == term["en"]:
        return []
    legacy = [bad for bad in term.get("avoid") or [] if bad.lower() in slot.lower()]
    if not legacy:
        return [f"第 4 步标题术语位 {slot!r} 不是 glossary en 值 {term['en']!r}"]
    rest = "\n".join(line for i, line in enumerate(lines) if i != heads[0]).lower()
    if any(bad.lower() in rest for bad in legacy):
        return []
    return [f"第 4 步标题术语位 {slot!r} 用了 glossary avoid 词 {legacy}(正文其余行已迁移), 应为 {term['en']!r}"]


def test_accept4_quickstart_mismatch_notes_removed():
    en = (REPO_ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    zh = (REPO_ROOT / "QUICKSTART.zh-CN.md").read_text(encoding="utf-8")
    assert "Note on the guide" not in en and "still being aligned" not in en
    assert "关于向导的说明" not in zh and "仍在向最新授权流对齐" not in zh
    # 其余正文不改: 两份仍按现行流程(常设授权 / 执行分工); 英文术语位按 glossary 取词(AIPOS-F281)
    term = _glossary_term(GRANT_TERM_ZH)
    problems = en_step4_heading_problems(en, term)
    _show(f"[验收④·英文第 4 步标题] glossary en={term['en']!r} avoid={term.get('avoid')} 判定={problems or '合格'}")
    assert not problems, problems
    assert "## 第 4 步 —— 签项目常设授权(Owner)" in zh


def test_accept4_en_step4_heading_judgement_follows_glossary():
    """判定随 glossary 走: 新词合格; 旧译仅在全文未迁移时容忍; 正文已迁移而标题仍旧译 / 非术语词 / 骨架缺失 = 判红。"""
    term = _glossary_term(GRANT_TERM_ZH)
    assert term.get("avoid"), "glossary 该条未声明 avoid, 本判定无旧译可核"
    bad = term["avoid"][0]
    new_head = f"{EN_STEP4_PREFIX}{term['en']}{EN_STEP4_SUFFIX}"
    old_head = f"{EN_STEP4_PREFIX}project {bad}{EN_STEP4_SUFFIX}"
    cases = {
        "新词 + 正文已迁移": (f"one {term['en']}\n{new_head}\nbody", True),
        "新词 + 正文仍有旧译": (f"one project {bad}\n{new_head}\nbody", True),
        "旧译 + 正文仍有旧译(过渡期)": (f"one project {bad}\n{old_head}\nbody", True),
        "旧译 + 正文已迁移": (f"one {term['en']}\n{old_head}\nbody", False),
        "非术语词": (f"{EN_STEP4_PREFIX}project permit{EN_STEP4_SUFFIX}", False),
        "缺 (Owner)": (f"{EN_STEP4_PREFIX}{term['en']}", False),
        "无第 4 步": ("## Step 3 — x", False),
        "两行第 4 步": (f"{new_head}\n{new_head}", False),
    }
    got = {name: not en_step4_heading_problems(text, term) for name, (text, _) in cases.items()}
    _show("[F281·判定表] " + json.dumps(got, ensure_ascii=False))
    assert got == {name: ok for name, (_, ok) in cases.items()}
