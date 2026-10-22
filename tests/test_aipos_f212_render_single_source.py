"""AIPOS-F212 — 碎片化整理 D(F208 P1-3 / P1-4 / P2-7 部分): 开工稿 / 章程渲染单源。

件① 验证根节单源: lybra kickoff 复用开工面 next_resolver.card_workstation_view 的 validation_root_lines 与拒因(删重算); 沙箱按
    loop_driver.card_harness 统一判(卡面无 harness 时 /go 不再列全部沙箱); 渲染失败两面同码 VALIDATION_ROOT_UNRESOLVED(kickoff 给出口不抛异常)。
件② 硬规矩单源: 门交互硬规矩节只由 hard_rules_extractor.gate_hard_rules 产出; 章程 = render_hard_rules_for_charter 经占位
    {{hard_rules_section}}(三份母本删条文), 开工稿同读——同序同数; 未声明 hard_rules_source = UNDECLARED_REF + roles.schema fallback。
件③ 审计时限单源: 时限句只由 subagent_kickoff.runall_timing 产出(章程 = charter 态, 母本占位 {{runall_timing_charter}}, 删写死句);
    verbs wait_timeout 修法指针改 lybra kickoff。
件④ 文案单源(部分): 基线三态标签 verbs.schema lybra_regression_baseline.state_labels 一处声明(章程三态条 = charter_state_rules 渲染);
    run-long 门槛短语 = subagent_kickoff.run_long_threshold(与声明的单次工具时限同口径, 删「预计超过 5 分钟」)。
棘轮 R10: 角色母本 AGENTS.md 不得出现门交互硬规矩条文原文、基线三态标签原文、run-long / 时限数字句(基线 tests/f212_master_static_copy_baseline.jsonl,
    只减不增)。

靶场 = 临时治理根(AIPOS-F190 chris 形验证根 rig / AIPOS-F189 kickoff rig / AIPOS-F80 章程 rig); 不读真实凭据、不碰真实治理根、
不对真实门写、不起 pi。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ratchet_baseline import read_entries  # noqa: E402
import test_aipos_f190_validation_root as f190  # noqa: E402  — chris 形验证根靶场唯一来源
from test_aipos_f190_validation_root import AUD, CARD, EXEC, _cli, _declare_both, _show, rig  # noqa: E402,F401
from tools.aipos_cli.verb_contract import declared_exit_code  # noqa: E402

BASELINE_PATH = Path(__file__).resolve().parent / "f212_master_static_copy_baseline.jsonl"
MASTERS = ("executor", "auditor", "advisor")
VR_TITLE = "## 跨机临时验证根"


# ===========================================================================
# 件① 验证根节: 四形态下 /go(my-tasks next_card)与 lybra kickoff 两面一致
# ===========================================================================

def _card_path(gov: Path) -> Path:
    return gov / "5_tasks" / "queue" / "claimed" / f"{CARD.lower()}.md"


def _set_card_harness(gov: Path, harness: str | None) -> None:
    path = _card_path(gov)
    text = re.sub(r"^harness: .*\n", "", path.read_text(encoding="utf-8"), flags=re.M)
    if harness:
        text = text.replace("\ntask_mode:", f"\nharness: {harness}\ntask_mode:", 1)
    path.write_text(text, encoding="utf-8")


def _set_declaration_valid(gov: Path, valid: bool) -> None:
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    entry = data["validation_roots"]["declarations"][0]
    if valid:
        entry.setdefault("revoke_reason", None)
    else:
        entry.pop("revoke_reason", None)  # 形坏: 键不全 = VR_DECLARATION_INVALID
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _go_face(gov: Path) -> dict:
    """工位 /go 读的开工面(my-tasks --task-id --json): 入选 = next_card 验证根行; 不入选 = next_card_excluded 拒因。"""
    rc, out, err = _cli("--workspace-root", gov, "my-tasks", "--actor", EXEC, "--task-id", CARD, "--json")
    assert rc == 0, err
    data = json.loads(out)
    if data.get("next_card"):
        return {"lines": list(data["next_card"].get("validation_root_lines") or []), "kickoff": data["next_card"]["kickoff"]}
    excluded = next(e for e in data["next_card_excluded"] if e["task_id"] == CARD)
    return {"code": excluded["code"], "reason": excluded["reason"]}


def _kickoff_face(gov: Path) -> dict:
    rc, brief, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    if rc != declared_exit_code("lybra_kickoff", "ok"):
        return {"rc": rc, "err": err.strip()}
    rc, out, err = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov, "--json")
    assert rc == 0, err
    data = json.loads(out)
    return {"rc": 0, "text": brief, "lines": data["values"].get("validation_root_lines", "").split("\n"), "harness": data["values"]["harness"]}


def _sandbox_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if "沙箱可写根(" in ln]  # 措辞 verbs.schema lybra_kickoff.validation_root_texts.sandbox


def test_item1_four_shapes_go_and_kickoff_same_validation_section_and_refusal(rig):
    gov = rig["gov"]
    _declare_both(rig)
    outcomes = {}
    for harness in ("codex", None):
        for valid in (True, False):
            _set_card_harness(gov, harness)
            _set_declaration_valid(gov, valid)
            go, kick = _go_face(gov), _kickoff_face(gov)
            shape = f"卡面 harness={harness or '(无)'} / 验证根{'可' if valid else '不可'}渲染"
            outcomes[shape] = (go, kick)
            if valid:
                _show(f"---- 件①[{shape}] /go 验证根节(沙箱行)----\n" + "\n".join(_sandbox_lines(go["lines"]))
                      + f"\n---- 件①[{shape}] lybra kickoff 验证根节(沙箱行; 卡 harness 推导 = {kick['harness']})----\n"
                      + "\n".join(_sandbox_lines(kick["lines"])))
                assert kick["rc"] == 0 and go["lines"] and go["lines"] == kick["lines"], shape  # 两面逐行相同
                assert "\n".join(go["lines"]) in go["kickoff"] and "\n".join(kick["lines"]) in kick["text"]
                expected = harness or "pi"  # 卡面无 harness = card_harness 推出(本项目未声明 execution = 按 task_mode 缺省 pi)
                assert kick["harness"] == expected
                sandboxes = _sandbox_lines(go["lines"])
                assert sandboxes and all(f"沙箱可写根({expected})" in ln for ln in sandboxes), sandboxes
                assert len(sandboxes) == 1, "沙箱按卡 harness 只列一个(卡面无 harness 时 /go 不再列全部沙箱)"
            else:
                _show(f"---- 件①[{shape}] /go 不入选拒因 ----\n{go['code']}: {go['reason']}"
                      f"\n---- 件①[{shape}] lybra kickoff 拒因(exit {kick['rc']})----\n{kick['err']}")
                assert go["code"] == "VALIDATION_ROOT_UNRESOLVED" and "VR_DECLARATION_INVALID" in go["reason"]
                assert kick["rc"] == declared_exit_code("lybra_kickoff", "refused"), "kickoff 给出口, 不抛异常"
                reason = go["reason"].removesuffix("; 按 block-and-report 上报")
                assert "KICKOFF_VALIDATION_ROOT_UNRESOLVED" in kick["err"] and f"(VALIDATION_ROOT_UNRESOLVED): {reason}" in kick["err"]
                assert "出口:" in kick["err"]
    _set_declaration_valid(gov, True)
    _set_card_harness(gov, "codex")


def test_item1_kickoff_reads_view_no_recompute():
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert "render_section_lines" not in src.split('"""', 2)[2] and "card_harness(" not in src, "kickoff 不再自算验证根节 / harness"
    assert 'view.get("validation_root_lines")' in src and 'view.get("validation_root_refusal")' in src
    nr = (REPO_ROOT / "tools" / "aipos_cli" / "next_resolver.py").read_text(encoding="utf-8")
    assert 'fm.get("harness")' not in nr, "开工面不读卡面 harness 原值(统一经 card_harness)"


# ===========================================================================
# 件② 硬规矩: lybra charter 与 lybra kickoff 同序同数(未声明 / 声明两态)
# ===========================================================================

def _charter_rules(gov: Path, role: str, instance: str) -> tuple[list[str], str]:
    rc, text, err = _cli("charter", "--role", role, "--instance", instance, "--workspace-root", gov)
    assert rc == 0, err
    start = text.index("## 🟡 硬规矩(门交互与职责边界")
    section = text[start:text.index("\n---", start)]
    return [ln for ln in section.splitlines() if re.match(r"\d+\. \*\*", ln)], section


def _kickoff_rules(gov: Path, card: str) -> tuple[list[str], str]:
    rc, brief, err = _cli("kickoff", "--task-id", card, "--workspace-root", gov)
    assert rc == 0, err
    start = brief.index("门交互硬规矩(单源 ")
    lines = brief[start:].splitlines()
    rules = []
    for ln in lines[1:]:
        if not re.match(r"  \d+\. \*\*", ln):
            break
        rules.append(ln[2:])
    return rules, "\n".join([lines[0], *("  " + r for r in rules)])


def test_item2_charter_and_kickoff_hard_rules_same_order_and_count(rig):
    from tools.aipos_cli.hard_rules_extractor import UNDECLARED_REF

    gov = rig["gov"]
    for declared in (False, True):
        if declared:
            handbook = gov / "manuals" / "rules.md"
            handbook.parent.mkdir(parents=True, exist_ok=True)
            handbook.write_text("# 手册\n\n## 0.5. 硬规矩\n\n1. **靶场规矩甲** — 说明甲。\n2. **靶场规矩乙** — 说明乙。\n"
                                "3. **靶场规矩丙** — 说明丙。\n4. **靶场规矩丁** — 说明丁。\n5. **靶场规矩戊** — 说明戊。\n"
                                "6. **靶场规矩己** — 说明己。\n\n**实撞背景**:靶场。\n\n## 1. 其他\n", encoding="utf-8")
            data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
            data.setdefault("paths", {})["hard_rules_source"] = "manuals/rules.md"
            (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        charter_rules, charter_section = _charter_rules(gov, "executor", EXEC)
        kickoff_rules, kickoff_section = _kickoff_rules(gov, CARD)
        _show(f"---- 件②[{'声明 manuals/rules.md' if declared else '未声明 hard_rules_source'}] lybra charter 硬规矩节 ----\n{charter_section}"
              f"\n---- 同项目 lybra kickoff 门交互硬规矩 ----\n{kickoff_section}")
        assert charter_rules and charter_rules == kickoff_rules, "同序同数同文"
        if declared:
            assert len(charter_rules) == 6 and "manuals/rules.md § 0.5" in charter_section and "manuals/rules.md § 0.5" in kickoff_section
        else:
            assert len(charter_rules) == 5 and UNDECLARED_REF in charter_section and UNDECLARED_REF in kickoff_section


def test_item2_declared_but_unreadable_fails_closed_both_faces(rig):
    gov = rig["gov"]
    data = json.loads((gov / "project.json").read_text(encoding="utf-8"))
    data.setdefault("paths", {})["hard_rules_source"] = "manuals/missing.md"
    (gov / "project.json").write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    rc_c, _o, err_c = _cli("charter", "--role", "executor", "--instance", EXEC, "--workspace-root", gov)
    rc_k, _o, err_k = _cli("kickoff", "--task-id", CARD, "--workspace-root", gov)
    _show(f"[件② 声明了读不出] lybra charter exit {rc_c}: {err_c.strip()}\n[件② 声明了读不出] lybra kickoff exit {rc_k}: {err_k.strip()}")
    assert rc_c != 0 and rc_k == declared_exit_code("lybra_kickoff", "unreadable")
    for err in (err_c, err_k):
        assert "硬规矩来源 manuals/missing.md § 0.5 不可用" in err and "出口:" in err


def test_item2_masters_carry_only_placeholder():
    roles_decl = json.loads((REPO_ROOT / "schema" / "roles.schema.json").read_text(encoding="utf-8"))["gate_hard_rules"]
    for role in MASTERS:
        text = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        assert text.count("{{hard_rules_section}}") == 1 and roles_decl["section_title"] not in text and "实撞背景" not in text, role
    src = (REPO_ROOT / "tools" / "aipos_cli" / "subagent_kickoff.py").read_text(encoding="utf-8")
    assert "gate_hard_rules(" in src and "extract_hard_rules_from_handbook" not in src


# ===========================================================================
# 件③ 审计时限句: 只由 runall_timing 产出(按 harness 两口径 + 章程态), 母本无写死句; wait_timeout 指针
# ===========================================================================

def test_item3_timing_sentences_single_producer(tmp_path, monkeypatch):
    import test_aipos_f189_small_sweep as f189
    from test_aipos_f99_audit_runall_baseline import AUDITOR_MASTER, _auditor_ctx, _make_gov
    from tools.aipos_cli.charter_render import render_charter

    seen = {}
    for card, harness in (("HBX-91", "claude-code"), ("HBX-92", "pi")):
        gov = f189._kickoff_rig(tmp_path, monkeypatch, card=card, harness=harness)
        rc, brief, err = _cli("kickoff", "--task-id", card, "--workspace-root", gov)
        assert rc == 0, err
        seen[harness] = [ln for ln in brief.splitlines() if ln.startswith(("- 全量命令: ", "- 预计超过", "- 本卡 harness"))]
        _show(f"---- 件③ harness={harness} 开工稿时限句 / run-long 门槛原文 ----\n" + "\n".join(seen[harness]))
    assert "超过本卡 harness claude-code 的单次工具时限 600 秒" in seen["claude-code"][0]
    assert seen["claude-code"][1].startswith("- 预计超过本卡 harness claude-code 单次工具时限 600 秒的命令用 run-long")
    assert "本卡 harness pi 未声明单次工具时限" in seen["pi"][0] and seen["pi"][1].startswith("- 本卡 harness pi 未声明单次工具时限")
    gov = _make_gov(tmp_path, monkeypatch, project="otherproj", test_contract={"runall_path": "ci/all.sh"})
    rendered = render_charter(AUDITOR_MASTER.read_text(encoding="utf-8"), _auditor_ctx(tmp_path, gov, "otherproj"))
    line = next(ln for ln in rendered.splitlines() if "**全量时限**" in ln)
    _show(f"---- 件③ 审计章程全量时限句(章程态)原文 ----\n{line}")
    assert "以开工稿「验证」节为准" in line and "一律 run-long" in line
    master = AUDITOR_MASTER.read_text(encoding="utf-8")
    assert "{{runall_timing_charter}}" in master and "禁取更短值" not in master and "显式传 timeout**" not in master
    hits = sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "tools").rglob("*.py")
                  if re.search(r"runall_timing_(sync|run_long|undeclared)|texts\[.runall_timing_charter|run_long_threshold_(charter|declared|undeclared)", p.read_text(encoding="utf-8", errors="ignore")))
    assert hits == ["tools/aipos_cli/subagent_kickoff.py"], hits


def test_item3_wait_timeout_fix_points_at_kickoff():
    verbs = json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))
    fix = json.dumps(_find_wait_timeout(verbs), ensure_ascii=False)
    _show(f"[件③ wait_timeout 修法] {fix}")
    assert "lybra kickoff --task-id {task_id}" in fix and "my-tasks --actor" not in fix


def _find_wait_timeout(node):
    if isinstance(node, dict):
        if isinstance(node.get("wait_timeout"), dict) and "fix" in node["wait_timeout"]:
            return node["wait_timeout"]["fix"]
        for value in node.values():
            found = _find_wait_timeout(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_wait_timeout(value)
            if found is not None:
                return found
    return None


# ===========================================================================
# 件④ 三态标签单源 + 母本渲染体积(守卫内)
# ===========================================================================

def test_item4_baseline_labels_single_declaration():
    from tools.aipos_cli.post_merge_regression import baseline_state_labels, baseline_state_rules

    labels = baseline_state_labels()
    assert labels == {"recorded": "已记录, 可复用", "pending": "合并后回归在跑", "self_run_required": "须自跑"}
    for role in ("executor", "auditor"):
        rules = baseline_state_rules(role)
        assert all(f"「{label}」" in rules for label in labels.values()), role
    assert baseline_state_rules("advisor") is None
    for rel in ("tools/aipos_cli/post_merge_regression.py", "tools/aipos_cli/subagent_kickoff.py", "tools/aipos_cli/audit_derivation.py",
                "tools/aipos_cli/charter_render.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "已记录, 可复用" not in src and "合并后回归在跑(" not in src, rel
    texts = json.dumps(json.loads((REPO_ROOT / "schema" / "verbs.schema.json").read_text(encoding="utf-8"))["verbs"]["lybra_kickoff"]["texts"],
                       ensure_ascii=False)
    assert "已记录, 可复用" not in texts and "预计超过 5 分钟" not in texts


def test_item4_rendered_masters_within_size_guard(tmp_path, monkeypatch):
    from test_aipos_f80_zero_gate_charter import _identity, _make_gov
    from tools.aipos_cli.charter_render import charter_render_context, charter_section_sizes, charter_size_guard, render_charter

    guard = charter_size_guard()
    gov = _make_gov(tmp_path, monkeypatch, shape="lybra")
    prefixes = {"executor": "exec", "auditor": "audit", "advisor": "advisor"}
    for role in MASTERS:
        harness = tmp_path / "pi" / f"lybra-{role}"
        harness.mkdir(parents=True)
        ctx = charter_render_context(gov, identity=_identity(harness, role, f"{prefixes[role]}.lybra.hostl", "lybra"), product_commit="x")
        master = (REPO_ROOT / "agents" / "roles" / role / "AGENTS.md").read_text(encoding="utf-8")
        rendered = render_charter(master, ctx)
        size = len(rendered.encode("utf-8"))
        hard = next((n for title, n in charter_section_sizes(rendered) if "硬规矩(门交互" in title), None)
        _show(f"[件④ 体积] {role}: 母本 {len(master.encode('utf-8'))} 字节 → 渲染物 {size} 字节(守卫 {guard['max_bytes']}); 硬规矩节 {hard} 字节")
        assert size < guard["max_bytes"] and "{{" not in rendered


# ===========================================================================
# 棘轮 R10: 角色母本静态复制(只减不增)
# ===========================================================================

_TIME_SENTENCE = re.compile(r"\d+\s*(秒|分钟|s\b|min\b)")
_TIME_TOPIC = re.compile(r"run-long|时限|timeout|超时")


def _r10_patterns() -> dict[str, list[str]]:
    from tools.aipos_cli.hard_rules_extractor import gate_hard_rules_declaration
    from tools.aipos_cli.post_merge_regression import baseline_state_labels

    titles = set()
    for rules in gate_hard_rules_declaration()["fallback"]["rules_by_role_class"].values():
        for rule in rules:
            match = re.search(r"\*\*(.+?)\*\*", rule)
            if match:
                titles.add(f"**{match.group(1)}**")
    labels = baseline_state_labels()
    label_needles = {f"「{label}」" for label in labels.values()} | {labels["recorded"], labels["pending"]}
    return {"gate_rule": sorted(titles), "baseline_label": sorted(label_needles)}


def scan_master(rel: str, text: str, patterns: dict[str, list[str]]) -> list[dict[str, str]]:
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        for kind, needles in patterns.items():
            needle = next((n for n in needles if n in line), None)  # 一行一类只记一次
            if needle is not None:
                hits.append({"file": rel, "kind": kind, "needle": needle, "fp": _fp(stripped), "text": stripped[:160]})
        if _TIME_TOPIC.search(line) and _TIME_SENTENCE.search(line):
            hits.append({"file": rel, "kind": "time_sentence", "needle": _TIME_SENTENCE.search(line).group(0), "fp": _fp(stripped),
                         "text": stripped[:160]})
    return hits


def _fp(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def scan_masters() -> list[dict[str, str]]:
    patterns = _r10_patterns()
    hits = []
    for role in MASTERS:
        rel = f"agents/roles/{role}/AGENTS.md"
        hits.extend(scan_master(rel, (REPO_ROOT / rel).read_text(encoding="utf-8"), patterns))
    return hits


def _key(entry: dict[str, object]) -> tuple[str, str, str, str]:
    return (str(entry["file"]), str(entry["kind"]), str(entry["needle"]), str(entry["fp"]))


def test_r10_baseline_well_formed():
    for entry in read_entries(BASELINE_PATH):
        assert set(entry) == {"file", "kind", "needle", "fp", "text", "reason"}, entry
        assert str(entry["reason"]).strip(), entry


def test_r10_master_static_copy_baseline_only_shrinks():
    hits = scan_masters()
    baseline = read_entries(BASELINE_PATH)
    current, allowed = Counter(_key(h) for h in hits), Counter(_key(e) for e in baseline)
    new, stale = current - allowed, allowed - current
    _show(f"[棘轮 R10] 扫描三份角色母本; 命中 {sum(current.values())} 处; 基线 {len(baseline)} 条")
    for hit in hits:
        _show(f"  命中 [{hit['kind']}] {hit['file']} {hit['needle']}: {hit['text']}")
    assert not new, ("角色母本新增静态复制(门交互硬规矩条文 / 基线三态标签 / run-long·时限数字句一律经渲染占位产出: "
                     "{{hard_rules_section}} / {{regression_baseline_states}} / {{runall_timing_charter}} / {{run_long_threshold}}): "
                     + "; ".join(f"[{h['kind']}] {h['file']} {h['text']}" for h in hits if _key(h) in new))
    assert not stale, f"基线条目已不存在(已修 / 已删)→ 从 {BASELINE_PATH.name} 删除这些行(只减不增): {sorted(stale)}"


def test_r10_pattern_catches_pre_f212_shapes():
    patterns = _r10_patterns()
    old = ("1. **你不接触门** — 不调用任何 `lybra_*` 动词\n"
           "  - 「已记录, 可复用」= 直接引用该记录\n"
           "  - 「须自跑」= 在草稿目录\n"
           "- **长验证命令用 run-long(AIPOS-F179)**:预计超过 5 分钟的验证命令\n"
           "  - pi 的 bash 工具没有内置时限, timeout 由你自己传(10-16 实撞的「420 秒上限」)\n"
           "- 被审 tip 一侧必须自跑(独立性所在)\n")
    kinds = Counter(h["kind"] for h in scan_master("x.md", old, patterns))
    assert kinds == Counter({"gate_rule": 1, "baseline_label": 2, "time_sentence": 2}), kinds  # 「必须自跑」不误伤
