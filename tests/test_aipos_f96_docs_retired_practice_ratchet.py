"""AIPOS-F96 件④: 「产品文档不教退役做法」只减不增棘轮夹具。

依据: governance/research/2026-10-04-fragmentation-recheck.md 条目 N3(docs 与仓根 README/QUICKSTART 教退役做法)。
现行做法: 推进 = 顾问 `lybra loop`; 工位只敲 /go; 接入 = `lybra onboarding guide` + `lybra project new`;
等待 = `lybra agent watch --workspace-root`。退役做法字面声明在下方 RETIRED_PRACTICES(新退役一项做法 = 加一行)。

扫描面(DOC_SCOPE): git ls-files 中 docs/**、README.md、QUICKSTART.md、agents/**/*.md、templates/**、skills/**(文本文件;
skills/ 由 AIPOS-F103 件② 加入: 仓根旧技能已删, 此后仓根若再出技能同样不许教退役做法)。
(templates/ 已随建项目单入口卡 AIPOS-F105 整目录删除, 扫描面保留以防回流; 删除不变量见 tests/test_aipos_f105_single_project_entry.py。)
现存命中登记在 tests/f96_docs_retired_practice_baseline.json(测试数据文件): 每条 = 文件 + 式样 id + 行指纹
(行文本 strip 后 sha1 前 12 位; 行号仅作定位参考, 不参与比对) + 复查条目号 + 留存理由。

判定(多重集比对, 键 = (file, pattern, fp)):
  - 现有命中不在基线 = 新增 → 红
  - 基线条目在仓里已不存在(已修 / 行文本已改)= 基线残留 → 红(修好即从基线删该条, 只减不增)
  - docs/ 零容忍: 基线不许有 docs/ 下条目; 其余条目只许是标 lane_blocked 的车道外文件
    (AIPOS-F105 删 templates/; AIPOS-F103 删 README/QUICKSTART 的「待退役」段——两处都不再是允许位置)

独立夹具而非并入 F87 棘轮(不变量 f)的理由: F87 的扫描面是产品代码(排除文档), 其基线 well-formed 断言写死不变量集合
{a..e}; 文档扫描面、式样与留存理由都不同, 独立成文件不改动 F87 夹具与其基线(与在途卡并行时互不冲突)。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "f96_docs_retired_practice_baseline.json"

# 退役做法字面(id → (正则, 现行做法/说明))。新退役一项做法 = 在此加一行, 并把现存命中登记进基线。
RETIRED_PRACTICES: dict[str, tuple[str, str]] = {
    "gate_client": (r"\bGateClient\b", "手写 GateClient 调门; 现行 = 产品命令(lybra loop 等)"),
    "owner_confirmed": (r"\bOWNER_CONFIRMED\b", "手按 Owner 确认令牌; 现行 = 信封授权下 lybra loop / Owner 凭据"),
    "confirm_client": (r"\bconfirm_client\b", "手调 confirm_client; 现行 = 产品命令"),
    "lybra_on_off_sync": (
        r"/lybra (?:on|off|sync)\b|(?<![\w/.-])lybra (?:on|off)\b",
        "/lybra on|off|sync 与 `lybra on|off` 接活模式; 现行 = 工位只敲 /go, 分发用 lybra sync 命令",
    ),
    "lybra_loop_extension": (r"\blybra-loop\b", "旧 pi 门循环扩展 lybra-loop; 现行 = 顾问 lybra loop + 工位 /go"),
    "executor_claim_return": (
        r"\blybra (?:claim|return)\b|\blybra_queue_(?:claim|return)_(?:dry_run|confirm)\b",
        "执行体自己认领 / 交回 / 确认; 现行 = 执行体零门, 由顾问 lybra loop 完成",
    ),
    "agent_watch_gate_url": (r"\bagent (?:watch|fetch)\b[^\n]*--gate-url", "agent watch/fetch --gate-url 门拉取; 现行 = agent watch --workspace-root"),
    "old_connector": (r"\bagent (?:fetch|materialize|pushback)\b", "旧跨机连接器(N2, AIPOS-F103 已退役: 子命令不存在)"),
    "lybra_dispatch": (r"\blybra dispatch\b", "lybra dispatch 执行体自认领(N2, AIPOS-F103 已退役: 子命令不存在)"),
    "lybra_init": (r"\blybra init\b|\bworkspace init\b", "旧建项目入口 init / workspace init(N1, AIPOS-F105 已退役, 命令已删); 现行 = onboarding guide + project new"),
}
COMPILED = {pid: re.compile(rx) for pid, (rx, _) in RETIRED_PRACTICES.items()}

# 基线里允许出现(非 lane_blocked)的位置: 无(templates/ 已随 AIPOS-F105 删除; README/QUICKSTART 待退役段已随 AIPOS-F103 删除)
ALLOWED_BASELINE_FILES: set[str] = set()


def fingerprint(line: str) -> str:
    return hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:12]


def in_doc_scope(rel: str) -> bool:
    if rel in ("README.md", "QUICKSTART.md"):
        return True
    if rel.startswith(("docs/", "templates/", "skills/")):
        return True
    return rel.startswith("agents/") and rel.endswith(".md")


def doc_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    files: list[str] = []
    for rel in out.split("\0"):
        if not rel or not in_doc_scope(rel):
            continue
        full = REPO_ROOT / rel
        if not full.is_file():
            continue
        raw = full.read_bytes()
        if b"\0" in raw[:8192]:  # 二进制(如 docs/assets/*.png)不扫
            continue
        files.append(rel)
    return sorted(files)


def scan_text(rel: str, text: str) -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pid, rx in COMPILED.items():
            if rx.search(line):
                hits.append({"file": rel, "line": lineno, "pattern": pid, "fp": fingerprint(line), "text": line.strip()[:160]})
    return hits


def current_hits() -> list[dict[str, object]]:
    hits: list[dict[str, object]] = []
    for rel in doc_files():
        hits.extend(scan_text(rel, (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")))
    return hits


def _key(item: dict[str, object]) -> tuple[str, str, str]:
    return (str(item["file"]), str(item["pattern"]), str(item["fp"]))


def load_baseline() -> dict[str, object]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def ratchet_diff(current: list[dict[str, object]], entries: list[dict[str, object]]) -> dict[str, list]:
    cur = Counter(_key(item) for item in current)
    base = Counter(_key(entry) for entry in entries)
    new_keys = cur - base
    stale_keys = base - cur
    return {
        "new": [item for item in current if new_keys.get(_key(item), 0) > 0],
        "stale": [entry for entry in entries if stale_keys.get(_key(entry), 0) > 0],
    }


# ------------------------------------------------------------------------------------------------
# 夹具
# ------------------------------------------------------------------------------------------------


def test_doc_scope_is_nonempty_and_covers_declared_surfaces():
    files = doc_files()
    assert "README.md" in files and "QUICKSTART.md" in files, files[:10]
    assert any(f.startswith("docs/") for f in files)
    assert any(f.startswith("agents/") for f in files)
    assert not [f for f in files if f.endswith(".png")]


def test_scan_scope_covers_repo_root_skills():
    """AIPOS-F103 件②: 扫描面含仓根 skills/**(旧技能已删; 再出现的技能文本同样受棘轮约束)。"""
    assert in_doc_scope("skills/any-skill/SKILL.md")
    hits = scan_text("skills/any-skill/SKILL.md", "lybra agent fetch --gate-url http://127.0.0.1:7118\n")
    assert {str(h["pattern"]) for h in hits} >= {"old_connector", "agent_watch_gate_url"}, hits
    assert ratchet_diff(current_hits() + hits, load_baseline()["entries"])["new"][-len(hits):] == hits


def test_baseline_is_well_formed_and_only_in_allowed_places():
    baseline = load_baseline()
    entries = baseline["entries"]
    assert baseline["count"] == len(entries), (baseline["count"], len(entries))
    for entry in entries:
        assert entry.get("file") and entry.get("fp") and isinstance(entry.get("line"), int), entry
        assert entry.get("pattern") in RETIRED_PRACTICES, entry
        assert entry.get("item") and entry.get("reason"), entry  # 每条带复查条目号与留存理由
        rel = str(entry["file"])
        assert not rel.startswith("docs/"), f"docs/ 零容忍, 基线不许登记: {entry}"
        allowed = rel in ALLOWED_BASELINE_FILES or entry.get("lane_blocked")
        assert allowed, f"基线条目只许是标 lane_blocked 的车道外文件: {entry}"


def test_ratchet_no_new_hits_and_no_stale_baseline_entries():
    current = current_hits()
    baseline = load_baseline()
    diff = ratchet_diff(current, baseline["entries"])
    by_pattern = Counter(str(item["pattern"]) for item in current)
    print(f"[F96 文档退役做法棘轮] 现存命中 {len(current)} 条, 按式样: {dict(sorted(by_pattern.items()))}; 基线 count={baseline['count']}")
    problems: list[str] = []
    for item in diff["new"]:
        problems.append(f"新增退役做法(不在基线; 改写为现行做法, 勿入基线): {json.dumps(item, ensure_ascii=False)}")
    for entry in diff["stale"]:
        problems.append(f"基线残留(仓内已不存在, 已修则须从 {BASELINE_PATH.name} 删除该条): {json.dumps(entry, ensure_ascii=False)}")
    assert not problems, "\n".join(problems)


def test_ratchet_detects_injected_hit_and_stale_entry_red():
    """负夹具: 人造一条新增命中与一条已修条目, 比对必须双双报红(棘轮不是摆设)。
    以「不注入」的比对结果为参照计增量, 故在任何基线状态下都只验探测器本身。"""
    current = current_hits()
    entries = load_baseline()["entries"]
    reference = ratchet_diff(current, entries)
    injected = list(current) + scan_text("docs/zz_injected.md", "执行体先 `lybra claim X-1` 再交回\n")
    assert len(injected) == len(current) + 1
    diff = ratchet_diff(injected, entries)
    assert len(diff["new"]) == len(reference["new"]) + 1
    assert "docs/zz_injected.md" in [str(item["file"]) for item in diff["new"]]
    # 模拟已修一条: 去掉一个与基线同键的现存命中(多重集只去一个)
    base_keys = {_key(entry) for entry in entries}
    idx = next((i for i, item in enumerate(current) if _key(item) in base_keys), None)
    if idx is not None:
        dropped = current[:idx] + current[idx + 1 :]
        assert len(ratchet_diff(dropped, entries)["stale"]) == len(reference["stale"]) + 1


def test_patterns_hit_retired_forms_and_spare_current_forms():
    """式样本身: 退役字面各至少命中一例; 现行做法字面一律不误报。"""
    retired = {
        "gate_client": "client = GateClient(url)",
        "owner_confirmed": "owner_confirmation_token: OWNER_CONFIRMED",
        "confirm_client": "门交互经 `confirm_client`",
        "lybra_on_off_sync": "the agent says **`lybra on`** to start; `/lybra sync` in pi",
        "lybra_loop_extension": "pi 扩展 lybra-loop 自动交回",
        "executor_claim_return": "lybra claim EXAMPLE-001 then lybra_queue_return_confirm",
        "agent_watch_gate_url": "lybra agent watch --gate-url http://127.0.0.1:7118 --token x",
        "old_connector": "lybra agent materialize --task-id X; lybra agent pushback",
        "lybra_dispatch": "lybra dispatch --task-id X",
        "lybra_init": "lybra init ./ws --project-id p; lybra workspace init",
    }
    assert set(retired) == set(RETIRED_PRACTICES)
    for pid, sample in retired.items():
        assert COMPILED[pid].search(sample), (pid, sample)
    current_forms = [
        "lybra loop --task-id X-1 --envelope pol_x",
        "lybra onboarding guide my_project",
        "lybra project new my_project",
        "lybra agent watch --workspace-root /g --timeout 600",
        "lybra sync --harness-root ~/w --workspace-root /g --dry-run",
        "Owner 在工位只敲 /go",
        "lybra tui --gate-url http://127.0.0.1:7118",
        "lybra governance-commit --task-id X --actor a",
        "lybra queue amend --task-id X",
    ]
    for line in current_forms:
        hit = [pid for pid, rx in COMPILED.items() if rx.search(line)]
        assert not hit, (line, hit)
