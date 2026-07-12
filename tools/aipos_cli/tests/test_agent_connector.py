"""AIPOS-248 — agent-side connector tests.

Pins (card §5, R hooks folded):
- fetch three-state classification/rendering, incl. the held-state SUPPRESSION of new
  offers (one-session-one-task discipline first) and the R-c per-state P-A guidance
  plus the R-b context-hygiene hint;
- the claimable predicate is an ADVISORY mirror of the gate validator's actor-match —
  consistency pinned against the REAL validator over a fixture matrix (R hook 3①):
  the client must never be NARROWER than the gate (too narrow = silently missed work);
- watch is a BOUNDED, foreground, client-side loop: interval floor is an error, hit and
  timeout both exit cleanly, error backoff doubles/caps/resets — with a fake sleeper
  (zero real waiting);
- red line 2 reverse test against a REAL gate: N stateless pulls leave the workspace
  byte-identical, responses carry no liveness vocabulary, and (R hook 5 positive probe)
  two consecutive pulls over an unchanged queue return identical payloads;
- structural pins: the connector source contains no write/confirm tool names, no timer
  primitives, and no role hardcoding outside docstrings (red lines 3/4).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator
from unittest.mock import patch

from tools.aipos_cli import agent_connector
from tools.aipos_cli.agent_connector import (
    STATE_CLAIMABLE,
    STATE_HELD,
    STATE_NONE,
    actor_matches,
    classify,
    fetch_once,
    render,
    run_watch,
)
from tools.aipos_cli.confirm_client import GateClient, GateError
from tools.aipos_cli.validator import validate_single_task
from tools.mcp_server.http_sse import DEFAULT_HTTP_HOST, HttpSseConfig, build_http_server

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _task(task_id: str, state: str, **metadata: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "queue_state": state,
        "frontmatter_status": state,
        "parse_errors": [],
        "metadata": metadata,
    }


class AgentConnectorClassifyTests(unittest.TestCase):
    # --- three states + rendering (card §3-Q3; R-b/R-c folded) ---

    def test_held_state_suppresses_new_offers(self) -> None:
        """一 session 一 task 前置:持有中 → 新任务列表被抑制(对'直接列出全部 pending'的
        朴素实现 RED——见卡 §9 变异实跑)。"""
        tasks = [
            _task("T-HELD", "claimed", claimed_by="me", agent_instance="me"),
            _task("T-NEW", "pending", assigned_to="me"),
        ]
        result = classify(tasks, "me")
        self.assertEqual(result["state"], STATE_HELD)
        out = render(result)
        self.assertIn("T-HELD", out)
        self.assertIn("一 session 一 task", out)
        self.assertNotIn("T-NEW", out, "held state must SUPPRESS new offers (discipline first)")
        self.assertIn("/clear", out, "R-b: the post-return hygiene step is part of the guidance")

    def test_o3_1_held_follows_claimed_by_not_assigned_to(self) -> None:
        """F-248-o3-1(O3 实测复现,诊断见 §9):assigned_to != claimed_by 的错配任务——
        持有者判定必须跟 claimed_by(谁真的领了)走,不能跟 assigned_to 走。修前 bug:
        dave(仅 assigned_to)被误判"已持有";carol(claimed_by/agent_instance)才是真正
        持有者。地面真相:queue_mutation.py:203 `_prepare_claim` 只写 claimed_by;
        gate 自己的持有者判定(validator.py:420-427)也只认 claimed_by,不认
        assigned_to,错配即 BLOCK "Task is claimed by another actor"。"""
        mismatched = _task(
            "T-MISMATCH", "claimed", assigned_to="dave", agent_instance="carol", claimed_by="carol"
        )
        dave_result = classify([mismatched], "dave")
        carol_result = classify([mismatched], "carol")
        self.assertEqual(
            dave_result["state"], STATE_NONE, "dave is only assigned_to, NOT the actual holder"
        )
        self.assertEqual(carol_result["state"], STATE_HELD, "carol is claimed_by — the real holder")

    def test_claimable_state_lists_hints_and_guides(self) -> None:
        tasks = [_task("T-A", "pending", assigned_to="me", title="Do the thing")]
        result = classify(tasks, "me")
        self.assertEqual(result["state"], STATE_CLAIMABLE)
        out = render(result)
        self.assertIn("T-A", out)
        self.assertIn("列表是建议,门才是真相", out)  # R hook 3 disclosure
        self.assertIn("/clear", out)  # R-b hygiene hint on every offer
        self.assertIn("active_session_id", out)  # session↔task binding taught in place
        self.assertIn("Owner", out)  # the gate stays with the Owner

    def test_none_state(self) -> None:
        tasks = [_task("T-X", "pending", assigned_to="someone-else")]
        result = classify(tasks, "me")
        self.assertEqual(result["state"], STATE_NONE)
        self.assertIn("暂无可认领", render(result))

    def test_every_state_carries_pa_guidance(self) -> None:
        """R-c:三态输出各自带 P-A 引导(你在哪/下一步敲什么)。"""
        held = classify([_task("T1", "claimed", claimed_by="me")], "me")
        claimable = classify([_task("T2", "pending", assigned_to="me")], "me")
        none = classify([], "me")
        for result in (held, claimable, none):
            self.assertIn("→ 下一步", render(result))
            self.assertIn("→ 下一步", render(result, watching=True))

    # --- R hook 3①: advisory mirror vs the REAL validator (never narrower) ---

    def test_mirror_predicate_never_narrower_than_validator(self) -> None:
        """谓词一致性钉:凡真 validator 不因 actor 不匹配而拦的组合,客户端谓词必须列出
        (过窄 = 静默漏任务,比过宽更糟——R 钩3 裁定的失败方向)。"""
        actor = "cc-exec-01"
        matrix = [
            {"assigned_to": actor},
            {"agent_instance": actor},
            {"claimed_by": actor},
            {"assigned_to": actor, "agent_instance": "other"},
            {"assigned_to": "other", "agent_instance": actor},
            {"assigned_to": "other"},
            {"agent_instance": "other"},
            {},
        ]
        mismatch_reason = "Current actor does not match assigned_to or agent_instance"
        for metadata in matrix:
            task = _task("T-MTX", "pending", **metadata)
            report = validate_single_task(task, current_actor=actor)
            validator_accepts = mismatch_reason not in (report.get("blocking_reasons") or [])
            client_lists = actor_matches(task, actor)
            if validator_accepts:
                self.assertTrue(
                    client_lists,
                    f"mirror narrower than the gate for metadata={metadata} (silently missed work)",
                )

    def test_json_output_is_slim_and_stateless_shaped(self) -> None:
        tasks = [_task("T-A", "pending", assigned_to="me", secret_field="never-echoed")]
        payload = json.loads(agent_connector._to_json(classify(tasks, "me")))
        self.assertEqual(payload["state"], STATE_CLAIMABLE)
        self.assertEqual(payload["claimable"][0]["task_id"], "T-A")
        self.assertNotIn("secret_field", json.dumps(payload))


class AgentWatchTests(unittest.TestCase):
    """watch = agent 自启 + 前台 + 有界 三件套(R 钩2);fake sleeper,零真实等待。"""

    def _args(self, **overrides: Any) -> SimpleNamespace:
        base = dict(
            gate_url="http://127.0.0.1:1",
            connection_json=None,
            token_env="LYBRA_TEST_TOKEN",
            role="executor",
            actor="me",
            interval=60.0,
            max_wait=1800.0,
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_interval_floor_is_an_error_not_a_silent_raise(self) -> None:
        with patch.object(agent_connector, "_connect", side_effect=AssertionError("must not connect")):
            with redirect_stdout(io.StringIO()) as out:
                rc = run_watch(self._args(interval=5.0))
        self.assertEqual(rc, 2)
        self.assertIn("floor", out.getvalue())

    def test_hit_on_first_poll_exits_zero(self) -> None:
        result = {"state": STATE_CLAIMABLE, "held": [], "claimable": [_task("T-A", "pending", assigned_to="me")]}
        with patch.object(agent_connector, "_connect", return_value=object()), patch.object(
            agent_connector, "fetch_once", return_value=result
        ):
            with redirect_stdout(io.StringIO()) as out:
                rc = run_watch(self._args(), sleeper=lambda s: self.fail("must exit before sleeping"))
        self.assertEqual(rc, 0)
        self.assertIn("T-A", out.getvalue())

    def test_bounded_timeout_exits_zero_with_pa(self) -> None:
        sleeps: list[float] = []
        clock_now = [0.0]

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            clock_now[0] += seconds

        none_result = {"state": STATE_NONE, "held": [], "claimable": []}
        with patch.object(agent_connector, "_connect", return_value=object()), patch.object(
            agent_connector, "fetch_once", return_value=none_result
        ):
            with redirect_stdout(io.StringIO()) as out:
                rc = run_watch(
                    self._args(interval=60.0, max_wait=300.0), sleeper=sleeper, clock=lambda: clock_now[0]
                )
        self.assertEqual(rc, 0)
        self.assertEqual(sleeps, [60.0] * 4, "bounded: sleeps stop before max-wait would be exceeded")
        self.assertIn("超时", out.getvalue())
        self.assertIn("→ 下一步", out.getvalue())

    def test_error_backoff_doubles_caps_and_resets(self) -> None:
        sleeps: list[float] = []
        clock_now = [0.0]

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            clock_now[0] += seconds

        hit = {"state": STATE_CLAIMABLE, "held": [], "claimable": [_task("T-A", "pending", assigned_to="me")]}
        outcomes = [GateError("down"), GateError("down"), hit]

        def fake_fetch(client: Any, actor: str) -> dict[str, Any]:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(agent_connector, "_connect", return_value=object()), patch.object(
            agent_connector, "fetch_once", side_effect=fake_fetch
        ):
            with redirect_stdout(io.StringIO()) as out:
                rc = run_watch(self._args(max_wait=3600.0), sleeper=sleeper, clock=lambda: clock_now[0])
        self.assertEqual(rc, 0)
        self.assertEqual(sleeps, [60.0, 120.0], "backoff doubles per failure; success ends the loop")
        self.assertEqual(out.getvalue().count("pull failed"), 2, "failures print honestly, never silent")

    def test_backoff_is_capped(self) -> None:
        sleeps: list[float] = []
        clock_now = [0.0]

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            clock_now[0] += seconds

        with patch.object(agent_connector, "_connect", side_effect=GateError("down")):
            with redirect_stdout(io.StringIO()):
                rc = run_watch(
                    self._args(interval=60.0, max_wait=2000.0), sleeper=sleeper, clock=lambda: clock_now[0]
                )
        self.assertEqual(rc, 2, "all-failure watch exits loudly at the bound")
        self.assertTrue(all(s <= agent_connector.BACKOFF_CAP_SECONDS for s in sleeps), sleeps)
        self.assertIn(agent_connector.BACKOFF_CAP_SECONDS, sleeps, "backoff reaches and holds the cap")

    # --- structural pins (red lines 3/4): read-only module, zero timers, role-agnostic ---

    def test_connector_source_has_no_write_tools_and_no_timers(self) -> None:
        """结构性只读钉:模块不含任何 `call_tool`/`preview`/`confirm` 调用(唯一工具通道 =
        `GateClient.queue_tasks` 读面,写路径在源码层面不存在)+ 零定时原语。
        (工具名可以出现在 P-A 引导文案里——教 agent 下一步敲什么不是调用。)"""
        src = Path(agent_connector.__file__).read_text(encoding="utf-8")
        for needle in (
            ".call_tool(",
            ".preview(",
            ".confirm(",
            "set_interval",
            "threading.Timer",
            "sched.",
        ):
            self.assertNotIn(needle, src, f"connector must stay read-only/timer-free: found {needle}")
        self.assertIn(".queue_tasks()", src, "the read tool is the ONLY gate call surface")

    def test_connector_code_is_role_agnostic(self) -> None:
        """红线 4:非 docstring 的代码字符串/标识符不得写死 executor(planner 片只换 token)。"""
        source = Path(agent_connector.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                self.assertNotIn("executor", node.value, f"role hardcode in code string: {node.value!r}")


class AgentConnectorGateTests(unittest.TestCase):
    """红线 2 反向测试(真 gate):无状态 pull 零落盘 + 无 liveness 词汇 + 响应字节稳定。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)
        for state in ("pending", "claimed", "completed", "blocked"):
            (self.repo_root / "5_tasks" / "queue" / state).mkdir(parents=True, exist_ok=True)
        (self.repo_root / "5_tasks" / "queue" / "pending" / "aipos-conn-fetch.md").write_text(
            "\n".join(
                [
                    "---",
                    "task_id: AIPOS-CONN-FETCH",
                    "title: Connector fetch test",
                    "project: lybra",
                    "assigned_to: cc-exec-01",
                    "agent_instance: cc-exec-01",
                    "context_bundle: cc-exec-01",
                    "task_mode: code",
                    "model_tier: L2",
                    "priority: medium",
                    "status: pending",
                    "created_by: tester",
                    "needs_owner: false",
                    "output_target: tools/aipos_cli/",
                    "artifact_policy: formal_write",
                    "session_policy: single_task_session",
                    "context_isolation: strict",
                    "artifact_scope: tools/aipos_cli/",
                    "memory_scope: connector tests",
                    "---",
                    "Connector stateless-pull test task.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @contextmanager
    def gate(self) -> Iterator[str]:
        config = HttpSseConfig(
            host=DEFAULT_HTTP_HOST,
            port=0,
            token="",
            keepalive_seconds=0.01,
            max_keepalive_events=1,
            service_role_registry={
                "executor-secret": {
                    "role": "executor",
                    "token_ref": "svc-executor",
                    "scopes": ["queue_claim", "queue_return"],
                    "expires_at": "2999-01-01T00:00:00Z",
                    "fingerprint": "sha256:execfp248",
                }
            },
        )
        env = {"AIPOS_WORKSPACE_ROOT": str(self.repo_root)}
        with patch.dict(os.environ, env, clear=True):
            httpd = build_http_server(config)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = httpd.server_address
                yield f"http://{host}:{port}"
            finally:
                httpd.shutdown()
                thread.join(timeout=2)
                httpd.server_close()

    def _tree_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.repo_root.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(self.repo_root)).encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def test_stateless_pull_zero_liveness(self) -> None:
        """三断言 + 钩5 正面探针:①N 次 pull 后工作区全树字节不变(正内容断言,非 proxy);
        ②应答无 liveness 词汇(键与值);③队列不变时连续两次 pull 载荷逐字节一致(无状态
        的正面证明)。"""
        before = self._tree_hash()
        with self.gate() as url:
            client = GateClient(url, "executor-secret")
            client.initialize()
            payloads = []
            for _ in range(3):
                structured = client.call_tool("lybra_queue_list", {})
                payloads.append(json.dumps(structured, sort_keys=True, ensure_ascii=False))
            result = fetch_once(client, "cc-exec-01")
        self.assertEqual(self._tree_hash(), before, "a stateless pull must leave the workspace byte-identical")
        self.assertEqual(payloads[0], payloads[1], "unchanged queue -> byte-identical responses (stateless)")
        self.assertEqual(payloads[1], payloads[2])
        lowered = payloads[0].lower()
        for word in ("online", "presence", "heartbeat", "last_seen", "liveness"):
            self.assertNotIn(word, lowered, f"liveness vocabulary must not appear: {word}")
        self.assertEqual(result["state"], STATE_CLAIMABLE)
        self.assertEqual(result["claimable"][0]["task_id"], "AIPOS-CONN-FETCH")

    def test_fetch_three_states_against_real_gate(self) -> None:
        with self.gate() as url:
            client = GateClient(url, "executor-secret")
            client.initialize()
            self.assertEqual(fetch_once(client, "cc-exec-01")["state"], STATE_CLAIMABLE)
            self.assertEqual(fetch_once(client, "somebody-else")["state"], STATE_NONE)


class SkillDeliverableTests(unittest.TestCase):
    """交付物钉:SKILL.md frontmatter/教学内容、README 软链一句、披露 row。"""

    def test_skill_md_frontmatter_and_teaching(self) -> None:
        skill = _REPO_ROOT / "skills" / "lybra-executor" / "SKILL.md"
        self.assertTrue(skill.is_file(), f"missing {skill}")
        text = skill.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "SKILL.md must open with YAML frontmatter")
        head = text.split("---", 2)[1]
        self.assertIn("name: lybra-executor", head)
        self.assertIn("lybra on", head, "the trigger phrase must live in the description frontmatter")
        # F-248-o3-3: the frontmatter DESCRIPTION is what drives skill-matching — it must NOT
        # prescribe the broken slash form there (cc's slash resolver only matches registered
        # command names, the skill's own is /lybra-executor; there is no separate "lybra"
        # command, so a literal "/lybra on" fails to resolve). The body MAY still mention the
        # slash form to explicitly explain the anti-pattern to the reader.
        self.assertNotIn("/lybra on", head, "frontmatter trigger must be the bare phrase, not the broken slash form")
        self.assertNotIn("/lybra off", head, "frontmatter trigger must be the bare phrase, not the broken slash form")
        self.assertIn("## `lybra on`", text, "section headers must teach the bare-phrase form")
        self.assertIn("## `lybra off`", text, "section headers must teach the bare-phrase form")
        for needle in (
            "lybra off",
            "/clear",  # R-b hygiene
            "一单一净上下文",
            "active_session_id",
            "列表是建议,门才是真相",  # R hook 3 disclosure
            "as recorded",
            "SCOPE_DENIED",
            "工具自检",  # F-248-o3-4
            "claude mcp add lybra",  # F-248-o3-4 self-mount guidance
        ):
            self.assertIn(needle, text, f"SKILL.md missing teaching: {needle}")

    def test_readme_and_setup_reference_the_skill(self) -> None:
        readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("skills/lybra-executor", readme)
        self.assertIn("**`lybra on`**", readme, "README must teach the bare phrase as the actual trigger")
        setup = (_REPO_ROOT / "docs" / "mcp-agent-setup.md").read_text(encoding="utf-8")
        self.assertIn("skills/lybra-executor", setup)
        self.assertIn("F-248-o3-3", setup, "the real-machine finding must be disclosed in the setup doc")

    def test_disclosure_row_present(self) -> None:
        disclosure = (_REPO_ROOT / "docs" / "v1_disclosure.md").read_text(encoding="utf-8")
        self.assertIn("Agent connector is pull-only", disclosure)
        self.assertIn("as recorded — not live", disclosure)


if __name__ == "__main__":
    unittest.main()
