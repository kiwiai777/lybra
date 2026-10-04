"""AIPOS-F104 件④⑤: 「枚举声明值域 = 代码校验值域」不变量夹具(族 C-b: M6/M7/M16/N6)。

声明侧唯一真相: schema/enums.schema.json(queue_state / task_class / role_category / progress_status)、
schema/roles.schema.json roles[].role、schema/transitions.schema.json queue_mutations。
代码侧唯一投影:
  - 队列状态      task_loader.QUEUE_STATES(queue_dir=true)/ QUEUE_SKELETON_STATES(skeleton=true)
  - 搬卡转移表    queue_mutation.queue_mutation_transitions / ALLOWED_TRANSITIONS(transition_engine.validate_transition 已删)
  - task_class    task_complexity.task_class_declarations / ALLOWED_TASK_CLASSES, CLI draft create --task-class choices
  - role_category enums role_category == roles.schema roles[].role; naming_profile.ROLE_NAMES 投影 roles.schema
  - progress_status CLI task-progress --event-type choices、MCP lybra_task_progress inputSchema、两处校验

防回潮: AST 扫产品 .py, 手写字符串集合字面量若与上述枚举值域重合(全集, 或 ≥阈值的子集)= 红;
确属语义子集(偏好序/活跃态子集等, 非值域)的登记在 SEMANTIC_SUBSETS(带理由), 登记条目在仓里已不存在 = 红(清单只减不增)。
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import schema_loader  # noqa: E402
from tools.schema_loader import SchemaLoadError  # noqa: E402

ENUMS = json.loads((REPO_ROOT / "schema" / "enums.schema.json").read_text(encoding="utf-8"))["enums"]
ROLES = json.loads((REPO_ROOT / "schema" / "roles.schema.json").read_text(encoding="utf-8"))
TRANSITIONS = json.loads((REPO_ROOT / "schema" / "transitions.schema.json").read_text(encoding="utf-8"))
CONFIG = json.loads((REPO_ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))

# 受监控的枚举 → 子集告警阈值(手写集合元素数 ≥ 阈值且为该枚举值子集 = 疑似值域副本)
WATCHED_SUBSET_THRESHOLD = {"queue_state": 3, "task_class": 2, "role_category": 3, "progress_status": 3}

# 语义子集登记(file, enum, 排序后的值) → 理由。非值域副本: 偏好序 / 活跃态子集 / 角色子集。只减不增。
SEMANTIC_SUBSETS: dict[tuple[str, str, tuple[str, ...]], str] = {
    ("tools/aipos_cli/finalize.py", "queue_state", ("claimed", "completed", "pending")): "取卡摘要的查找范围(语义子集)",
    ("tools/aipos_cli/next_resolver.py", "queue_state", ("claimed", "completed", "pending")): "审计卡 <ID>R 已生成判定范围(语义子集)",
    ("tools/aipos_cli/next_resolver.py", "queue_state", ("blocked", "claimed", "pending")): "活跃卡扫描范围(语义子集)",
    ("tools/aipos_cli/board_login.py", "role_category", ("auditor", "executor", "owner", "owner-dispatch")): "看板登录角色偏好序",
    ("tools/aipos_cli/aipos_cli.py", "role_category", ("advisor", "owner", "planner")): "驱动方 token 角色偏好序(同块 4 处 = M1 碎片, 归族 B 卡)",
    ("tools/aipos_cli/onboarding.py", "role_category", ("advisor", "auditor", "executor")): "接入向导生成的三工位实例",
    ("tools/aipos_cli/workstation_wiring.py", "role_category", ("advisor", "auditor", "executor")): "有 harness 循环的角色类(LOOP_ROLE_CLASSES)",
}
# 同一 (file, enum, values) 允许出现的次数(缺省 1)
SEMANTIC_SUBSET_COUNTS = {("tools/aipos_cli/aipos_cli.py", "role_category", ("advisor", "owner", "planner")): 4}

EXCLUDED_DIR_PARTS = {"tests", "test", "fixtures", "__tests__", "playwright"}


def enum_values(name: str) -> list[str]:
    return [entry["value"] for entry in ENUMS[name]["values"]]


def queue_dir_values() -> tuple[str, ...]:
    return tuple(entry["value"] for entry in ENUMS["queue_state"]["values"] if entry.get("queue_dir") is True)


def product_py_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    files = []
    for rel in out.split("\0"):
        if not rel:
            continue
        path = Path(rel)
        if any(part in EXCLUDED_DIR_PARTS for part in path.parts[:-1]):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py") or path.name == "conftest.py":
            continue
        if (REPO_ROOT / rel).is_file():
            files.append(rel)
    return sorted(files)


def _str_literal_collections(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)) and len(node.elts) >= 2 and all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in node.elts
        ):
            yield node, {elt.value for elt in node.elts}


def scan_enum_domain_copies() -> list[tuple[str, str, tuple[str, ...], int]]:
    """手写字符串集合字面量 ∩ 枚举值域: 全集(任一枚举, 或队列目录投影)或受监控枚举 ≥阈值子集。"""
    domains = {name: set(enum_values(name)) for name in ENUMS}
    dir_domain = set(queue_dir_values())
    hits: list[tuple[str, str, tuple[str, ...], int]] = []
    for rel in product_py_files():
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node, values in _str_literal_collections(tree):
            for name, domain in domains.items():
                full = values == domain or (name == "queue_state" and values == dir_domain)
                watched = name in WATCHED_SUBSET_THRESHOLD and values <= domain and len(values) >= WATCHED_SUBSET_THRESHOLD[name]
                if full or watched:
                    hits.append((rel, name, tuple(sorted(values)), node.lineno))
    return hits


def _subparser_choices(parser: argparse.ArgumentParser, path: list[str], dest: str):
    current = parser
    for name in path:
        sub = next(a for a in current._actions if isinstance(a, argparse._SubParsersAction))
        current = sub.choices[name]
    action = next(a for a in current._actions if a.dest == dest)
    return list(action.choices)


class QueueStateProjectionTest(unittest.TestCase):
    def test_every_value_declares_bool_flags(self) -> None:
        for entry in ENUMS["queue_state"]["values"]:
            self.assertIsInstance(entry.get("queue_dir"), bool, entry)
            self.assertIsInstance(entry.get("skeleton"), bool, entry)
            if entry["skeleton"]:
                self.assertTrue(entry["queue_dir"], f"skeleton 目录必须是队列目录: {entry}")

    def test_task_loader_projection_equals_declaration(self) -> None:
        from tools.aipos_cli import task_loader

        self.assertEqual(task_loader.QUEUE_STATES, queue_dir_values())
        self.assertEqual(
            task_loader.QUEUE_SKELETON_STATES,
            tuple(e["value"] for e in ENUMS["queue_state"]["values"] if e.get("skeleton") is True),
        )
        self.assertNotIn("returned", task_loader.QUEUE_STATES)  # 记录推导态, 无目录(声明 queue_dir=false)

    def test_card_status_enum_ref_is_queue_state(self) -> None:
        card = json.loads((REPO_ROOT / "schema" / "card.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(card["fields"]["status"]["$enum"], "queue_state")

    def test_missing_flag_fails_closed(self) -> None:
        from tools.aipos_cli import task_loader

        broken = copy.deepcopy(schema_loader.load_schema("enums"))
        del broken["enums"]["queue_state"]["values"][0]["queue_dir"]
        with mock.patch.object(schema_loader, "load_schema", return_value=broken):
            with self.assertRaises(SchemaLoadError):
                task_loader._queue_state_projection("queue_dir")

    def test_former_copies_reference_projection(self) -> None:
        """M7 原 7 处副本(+ validator / deployment_authorization 两处同类)已改引用投影, 不再出现手写集合。"""
        expectations = {
            "tools/aipos_cli/task_loader.py": "QUEUE_STATES = _queue_state_projection(\"queue_dir\")",
            "tools/aipos_cli/workspace_config.py": "for state in QUEUE_SKELETON_STATES:",
            "tools/aipos_cli/orchestration_summary_preview.py": "from tools.aipos_cli.task_loader import QUEUE_STATES",
            "tools/aipos_cli/brief.py": "from tools.aipos_cli.task_loader import QUEUE_STATES",
            "web/board/app.py": "for state in QUEUE_STATES:",
            # flow_description 的队列副本随 AIPOS-F101 件① 删除第二推导核(_infer_task_status 等)一并消失, 不再是副本站点
            "tools/aipos_cli/project_structure.py": "from tools.aipos_cli.task_loader import QUEUE_SKELETON_STATES, queue_state_ref",
            "tools/aipos_cli/validator.py": "if queue_state not in QUEUE_STATES:",
        }
        for rel, needle in expectations.items():
            self.assertIn(needle, (REPO_ROOT / rel).read_text(encoding="utf-8"), rel)
        # AIPOS-F101: flow_description 不再查队列(无 find_task_card 调用, 也无手写状态集合)
        self.assertNotIn("find_task_card", (REPO_ROOT / "tools/aipos_cli/flow_description.py").read_text(encoding="utf-8"))


class TransitionTableSingleSourceTest(unittest.TestCase):
    def _declared_table(self) -> dict[str, tuple[tuple[str, ...], str]]:
        table = {}
        for action, spec in TRANSITIONS["queue_mutations"]["transitions"].items():
            source = TRANSITIONS["nodes"][spec["node"]] if "node" in spec else spec
            table[action] = (tuple(source["from_states"]), source["to_state"])
        return table

    def test_queue_mutation_reads_schema(self) -> None:
        from tools.aipos_cli import queue_mutation

        declared = self._declared_table()
        self.assertEqual(queue_mutation.queue_mutation_transitions(), declared)
        self.assertEqual(queue_mutation.ALLOWED_TRANSITIONS, declared)
        self.assertEqual(queue_mutation.REOPEN_SOURCE_STATES, declared["reopen"][0])
        self.assertEqual(declared["claim"], (tuple(TRANSITIONS["nodes"]["N1"]["from_states"]), TRANSITIONS["nodes"]["N1"]["to_state"]))

    def test_declared_states_are_queue_dirs(self) -> None:
        dirs = set(queue_dir_values())
        for action, (from_states, to_state) in self._declared_table().items():
            self.assertTrue(set(from_states) <= dirs and to_state in dirs, (action, from_states, to_state))
            self.assertNotIn("*", from_states)

    def test_validate_transition_retired(self) -> None:
        from tools.aipos_cli import transition_engine

        self.assertFalse(hasattr(transition_engine, "validate_transition"))
        self.assertTrue(callable(transition_engine.apply_transition_metadata))  # 9 个调用方, 保留

    def test_no_handwritten_transition_tables(self) -> None:
        actions = set(TRANSITIONS["queue_mutations"]["transitions"])
        offenders = []
        for rel in product_py_files():
            for node in ast.walk(ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))):
                if isinstance(node, ast.Dict):
                    keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if len(keys & actions) >= 3:
                        offenders.append(f"{rel}:{node.lineno}")
        self.assertEqual(offenders, [], "手写搬卡转移表(须读 transitions.schema queue_mutations)")

    def test_undeclared_or_non_dir_state_fails_closed(self) -> None:
        from tools.aipos_cli import queue_mutation

        missing = copy.deepcopy(schema_loader.load_schema("transitions"))
        del missing["queue_mutations"]
        bad_state = copy.deepcopy(schema_loader.load_schema("transitions"))
        bad_state["queue_mutations"]["transitions"]["withdraw"]["from_states"] = ["*"]
        for broken in (missing, bad_state):
            with mock.patch.object(schema_loader, "load_schema", return_value=broken):
                with self.assertRaises(SchemaLoadError):
                    queue_mutation.queue_mutation_transitions()


class RoleCategoryProjectionTest(unittest.TestCase):
    def test_role_category_equals_roles_schema(self) -> None:
        declared = enum_values("role_category")
        registry = [spec["role"] for spec in ROLES["roles"]]
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(set(declared), set(registry))
        self.assertIn("advisor", declared)
        self.assertEqual(ENUMS["role_category"].get("projection_of"), "roles.schema.json roles[].role")

    def test_config_role_ref_uses_role_category(self) -> None:
        role_field = CONFIG["configuration_sources"]["role"]["schema"]["role"]
        self.assertEqual(role_field["$enum"], "role_category")
        self.assertEqual(set(schema_loader.resolve_enum_ref("role_category")), {s["role"] for s in ROLES["roles"]})

    def test_naming_profile_role_names_projection(self) -> None:
        from tools.aipos_cli import naming_profile

        self.assertEqual(naming_profile.ROLE_NAMES, {spec["role"] for spec in ROLES["roles"]})
        # 原手写副本漏 advisor → advisor.advisor.<host> 曾放过「项目段不得是角色名」检查
        ok, reason = naming_profile.validate_instance_name("advisor.advisor.host", "advisor")
        self.assertFalse(ok)
        self.assertIn("is a role name", str(reason))


class TaskClassSingleSourceTest(unittest.TestCase):
    def test_code_domain_equals_declaration(self) -> None:
        from tools.aipos_cli import task_complexity
        from tools.aipos_cli.aipos_cli import build_parser

        declared = enum_values("task_class")
        self.assertEqual(list(task_complexity.ALLOWED_TASK_CLASSES), declared)
        self.assertEqual(list(task_complexity.task_class_declarations()), declared)
        self.assertEqual(_subparser_choices(build_parser(), ["draft", "create"], "task_class"), declared)
        self.assertIn(ENUMS["task_class"]["default"], declared)
        self.assertEqual(task_complexity.effective_task_class({}), ENUMS["task_class"]["default"])

    def test_all_three_values_usable(self) -> None:
        from tools.aipos_cli.task_complexity import validate_task_complexity

        base = {"task_mode": "docs", "assigned_to": "exec.p.h", "audit_by": "audit.p.h", "planner_agent": "plan.p.h", "reviewer": "rev.p.h"}
        for value in enum_values("task_class"):
            result = validate_task_complexity({**base, "task_class": value}, enforce_dependency_gate=True)
            self.assertEqual(result["blocking_reasons"], [], value)

    def test_standard_semantics(self) -> None:
        from tools.aipos_cli.task_complexity import suggest_workflow_roles, validate_task_complexity

        missing = validate_task_complexity({"task_class": "standard", "assigned_to": "exec.p.h"}, enforce_dependency_gate=True)
        self.assertEqual(missing["blocking_reasons"], ["Standard-class task missing audit_by"])
        same = validate_task_complexity({"task_class": "standard", "assigned_to": "x.p.h", "audit_by": "x.p.h"}, enforce_dependency_gate=True)
        self.assertEqual(same["blocking_reasons"], ["Standard-class assigned_to must not equal audit_by"])
        no_planner = validate_task_complexity(
            {"task_class": "standard", "assigned_to": "exec.p.h", "audit_by": "audit.p.h", "depends_on": ["X"]},
            enforce_dependency_gate=True,
        )
        self.assertEqual(no_planner["blocking_reasons"], [])  # 不要求 planner_agent/reviewer/依赖门
        self.assertEqual(suggest_workflow_roles({"task_class": "standard"})["suggested_workflow"], "2-role")

    def test_unknown_value_blocks_with_declared_domain(self) -> None:
        from tools.aipos_cli.task_complexity import validate_task_complexity

        result = validate_task_complexity({"task_class": "large"}, enforce_dependency_gate=False)
        self.assertEqual(result["blocking_reasons"], [f"task_class must be one of: {', '.join(enum_values('task_class'))} (enums.schema task_class)"])

    def test_missing_declaration_key_fails_closed(self) -> None:
        from tools.aipos_cli import task_complexity

        broken = copy.deepcopy(schema_loader.load_schema("enums"))
        del broken["enums"]["task_class"]["values"][1]["required_fields"]
        with mock.patch.object(schema_loader, "load_schema", return_value=broken):
            with self.assertRaises(SchemaLoadError):
                task_complexity.task_class_declarations()


class ProgressStatusSingleSourceTest(unittest.TestCase):
    def test_cli_and_mcp_domains_equal_declaration(self) -> None:
        from tools.aipos_cli.aipos_cli import build_parser
        from tools.mcp_server import tools as mcp_tools

        declared = enum_values("progress_status")
        self.assertEqual(_subparser_choices(build_parser(), ["task-progress"], "event_type"), declared)
        descriptors = [d for d in mcp_tools.WRITE_TOOL_DESCRIPTORS if d.get("name") == "lybra_task_progress"]
        self.assertEqual(len(descriptors), 1)
        self.assertEqual(descriptors[0]["inputSchema"]["properties"]["event_type"]["enum"], declared)


class NoHandwrittenEnumDomainTest(unittest.TestCase):
    def test_no_unregistered_enum_domain_copies(self) -> None:
        hits = scan_enum_domain_copies()
        current = Counter((rel, name, values) for rel, name, values, _line in hits)
        allowed = Counter({key: SEMANTIC_SUBSET_COUNTS.get(key, 1) for key in SEMANTIC_SUBSETS})
        new = sorted(
            f"{rel}:{line} {name} {list(values)}"
            for rel, name, values, line in hits
            if (rel, name, values) in (current - allowed)
        )
        stale = sorted(f"{key} x{count}" for key, count in (allowed - current).items())
        self.assertEqual(new, [], "手写枚举值域副本(改读 schema 投影; 确属语义子集才登记 SEMANTIC_SUBSETS 并写理由)")
        self.assertEqual(stale, [], "SEMANTIC_SUBSETS 登记条目已不存在(只减不增: 删掉该条)")


if __name__ == "__main__":
    unittest.main()
