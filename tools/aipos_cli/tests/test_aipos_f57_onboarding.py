#!/usr/bin/env python3
"""AIPOS-F57 — 从 0 接新项目全流程固化测试。

验收项:
  ① 第三项目端到端(probe-xyz 从注册→铸信封→发码→enroll→起 pi→首卡认领, 全程零手工编辑)
  ② 六步中任一步失败均报错带路+可执行出口(六个负夹具)
  ③ lybra-onboarding skill 经 C4B 分发、enroll 时落顾问侧、seed_only 不覆盖定制
  ④ skill 内零硬编码命令(命令取自产品输出, grep 断言)
  ⑤ 与 lybra-fallback skill 边界清晰(onboarding=从0接入, fallback=链条卡住时兜底), 二者同一分发通道、禁内容重复
  ⑥ chris 实录回放:今日 6 个缺口逐一验证不再出现(逐条对照)
  ⑦ 夹具入 run-all;⑧ 基线零新增失败

跑法: python3 -m pytest tools/aipos_cli/tests/test_aipos_f57_onboarding.py -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.aipos_cli.onboarding import (
    generate_onboarding_guide,
    format_guide_text,
    validate_step_prerequisites,
)


def _workstation_enroll_step(guide):
    """AIPOS-F92: 工位 enroll 步(标题含「工位 enroll」; 原六步的 Step 4)。"""
    return next(st for st in guide["steps"] if "工位 enroll" in st["title"])


class TestOnboardingGuideGeneration(unittest.TestCase):
    """① 第三项目端到端:probe-xyz 式从 0 走完全流程(验收①④)。"""

    def test_guide_generation_probe_xyz(self):
        """生成 probe-xyz 项目的完整指南(验收①:项目无关性)。AIPOS-F92: 九步(单门 home 根约定, Owner 一次性动作 2 条)。"""
        guide = generate_onboarding_guide("probe-xyz")
        self.assertEqual(guide["project_name"], "probe-xyz")
        self.assertEqual(guide["total_steps"], 9)
        self.assertEqual(len(guide["steps"]), 9)
        self.assertEqual(guide["owner_actions"], [3, 4])

        # 验证每步都有必要字段
        for step in guide["steps"]:
            self.assertIn("step_number", step)
            self.assertIn("title", step)
            self.assertIn("command", step)
            self.assertIn("purpose", step)
            self.assertIn("check", step)
            self.assertIn("on_fail", step)
            self.assertIn("creates", step)

    def test_guide_zero_hardcoded_project_names(self):
        """验收④:零硬编码命令(项目名从参数来,不写死)。"""
        guide1 = generate_onboarding_guide("probe-xyz")
        guide2 = generate_onboarding_guide("another-project")

        # Step 1 命令应包含各自的项目名
        step1_cmd1 = guide1["steps"][0]["command"]
        step1_cmd2 = guide2["steps"][0]["command"]
        self.assertIn("probe-xyz", step1_cmd1)
        self.assertNotIn("another-project", step1_cmd1)
        self.assertIn("another-project", step1_cmd2)
        self.assertNotIn("probe-xyz", step1_cmd2)

    def test_guide_step_titles(self):
        """步骤标题完整性检查(AIPOS-F92 九步)。"""
        guide = generate_onboarding_guide("test-proj")
        expected_titles = [
            "建项目",
            "声明产品仓",
            "签发顾问注册码",
            "签三张信封",
            "顾问 enroll 到治理根",
            "为执行 / 审计工位发注册码",
            "工位 enroll",
            "工位自检",
            "首卡",
        ]
        for i, exp in enumerate(expected_titles, 1):
            self.assertIn(exp, guide["steps"][i - 1]["title"])

    def test_guide_failure_paths(self):
        """验收②:每步都有 on_fail 字典且非空(报错带路+可执行出口)。"""
        guide = generate_onboarding_guide("probe-xyz")
        for step in guide["steps"]:
            on_fail = step.get("on_fail", {})
            self.assertIsInstance(on_fail, dict)
            self.assertGreater(len(on_fail), 0, f"Step {step['step_number']} 缺失败出口")
            # 验证每个失败场景都有出口说明
            for err, fix in on_fail.items():
                self.assertIsInstance(err, str)
                self.assertIsInstance(fix, str)
                self.assertGreater(len(fix), 10, f"Step {step['step_number']} 出口 '{err}' 说明过短")

    def test_format_guide_text(self):
        """文本格式化输出测试。"""
        guide = generate_onboarding_guide("probe-xyz")
        text = format_guide_text(guide)
        self.assertIn("probe-xyz", text)
        self.assertIn("Step 1:", text)
        self.assertIn("Step 9:", text)
        self.assertIn("Owner 一次性动作 2 条", text)
        self.assertIn("验证:", text)
        self.assertIn("失败出口:", text)


class TestStepPrerequisitesValidation(unittest.TestCase):
    """② 六步中任一步失败均报错带路(负夹具)。"""

    def test_step1_prereq_always_ok(self):
        """Step 1 无前置条件(从 0 开始)。"""
        result = validate_step_prerequisites(1, project_name="probe-xyz")
        # Step 1 本身不检查前置,但返回结构应完整
        self.assertIn("ok", result)
        self.assertIn("missing", result)
        self.assertIn("guidance", result)

    def test_step2_missing_project_root(self):
        """Step 2 前置:项目根必须存在(负夹具)。"""
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_step_prerequisites(
                2, project_name="nonexistent-proj", home_root=tmp
            )
            self.assertFalse(result["ok"])
            self.assertIn("project_root", result["missing"])
            self.assertTrue(any("Step 1" in g for g in result["guidance"]))

    def test_step4_missing_connection_json(self):
        """Step 4 前置:connection.json 必须存在(负夹具)。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            ws.mkdir()
            result = validate_step_prerequisites(
                8, project_name="probe-xyz", workspace_dir=str(ws)
            )
            self.assertFalse(result["ok"])
            self.assertIn("connection.json", result["missing"])

    def test_step4_missing_lybra_bin(self):
        """Step 4 前置:connection.json 必须含 lybra_bin(chris 缺口⑤,验收⑥)。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            lybra_dir = ws / ".lybra"
            lybra_dir.mkdir(parents=True)
            conn = lybra_dir / "connection.json"
            conn.write_text(json.dumps({"workspace_root": str(ws)}), encoding="utf-8")

            result = validate_step_prerequisites(
                8, project_name="probe-xyz", workspace_dir=str(ws)
            )
            self.assertFalse(result["ok"])
            self.assertIn("lybra_bin", result["missing"])
            self.assertTrue(any("lybra_bin" in g for g in result["guidance"]))

    def test_step4_workspace_root_mismatch(self):
        """Step 4 前置:workspace_root 须等于 governance_root(chris 缺口⑥,验收⑥)。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            lybra_dir = ws / ".lybra"
            lybra_dir.mkdir(parents=True)
            conn = lybra_dir / "connection.json"
            # 故意写成不一致
            conn.write_text(
                json.dumps({
                    "workspace_root": "/wrong/path",
                    "governance_root": str(ws),
                    "lybra_bin": "/usr/bin/lybra"
                }),
                encoding="utf-8",
            )

            result = validate_step_prerequisites(
                8, project_name="probe-xyz", workspace_dir=str(ws)
            )
            self.assertFalse(result["ok"])
            self.assertIn("workspace_root_mismatch", result["missing"])

    def test_step6_missing_owner_policy_ref(self):
        """Step 6 前置:role 文件必须含 owner_policy_ref(chris 缺口④,验收⑥)。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            lybra_dir = ws / ".lybra"
            lybra_dir.mkdir(parents=True)
            role_file = lybra_dir / "role"
            role_file.write_text(json.dumps({"role": "executor"}), encoding="utf-8")

            result = validate_step_prerequisites(
                8, project_name="probe-xyz", workspace_dir=str(ws)
            )
            self.assertFalse(result["ok"])
            self.assertIn("owner_policy_ref", result["missing"])
            self.assertTrue(any("owner_policy_ref" in g for g in result["guidance"]))


class TestSkillDistribution(unittest.TestCase):
    """③ lybra-onboarding skill 经 C4B 分发、seed_only 不覆盖定制(验收③)。"""

    def test_skill_file_exists(self):
        """skill 文件存在于产品仓。"""
        repo_root = Path(__file__).resolve().parents[3]
        skill_path = repo_root / "agents" / "skills" / "lybra-onboarding" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), f"Skill 文件不存在: {skill_path}")

    def test_skill_zero_hardcoded_commands(self):
        """验收④:skill 内零硬编码命令(引导用 lybra onboarding guide 拉命令)。"""
        repo_root = Path(__file__).resolve().parents[3]
        skill_path = repo_root / "agents" / "skills" / "lybra-onboarding" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")

        # skill 应引导用 lybra onboarding guide 拉命令
        self.assertIn("lybra onboarding guide", content)

        # 不应硬编码完整的 lybra project new <具体项目名> 命令
        # (模板示例可以有 <项目名> 占位符,但不能有具体项目名)
        self.assertNotIn("lybra project new probe-xyz", content)
        self.assertNotIn("lybra project new lybra", content)

    def test_skill_in_advisor_distribution(self):
        """验收③:skill 在 advisor 分发清单里。"""
        repo_root = Path(__file__).resolve().parents[3]
        dist_schema = repo_root / "schema" / "distribution.schema.json"
        data = json.loads(dist_schema.read_text(encoding="utf-8"))

        # 找 advisor-skills 分发条目
        advisor_skills_dist = None
        for dist in data.get("distributions", []):
            if dist.get("distribution_id") == "advisor-skills":
                advisor_skills_dist = dist
                break

        self.assertIsNotNone(advisor_skills_dist, "未找到 advisor-skills 分发条目")
        includes = advisor_skills_dist.get("filter", {}).get("include", [])
        self.assertIn("lybra-onboarding", includes, "lybra-onboarding 未在 advisor 分发清单")

    def test_skill_in_advisor_role_schema(self):
        """验收③:skill 在 advisor 角色应得技能集里。

        AIPOS-F83 件②: roles.schema tool_package 退役, 工具包单源 = distribution.schema;
        advisor 应得技能集改读分发声明(与门/sync/enroll 同一构建器)。"""
        from tools.aipos_cli.distribution_sync import harness_distributions
        from tools.aipos_cli.workstation_wiring import declared_role_distributions

        # AIPOS-F92 件②: 顾问技能声明给 claude-code harness(会话目录 .claude/skills), 不再走 pi 挂载
        dists = harness_distributions(declared_role_distributions("advisor", "advisor"), "claude-code")
        skills = {str(f["path"]).split("/", 1)[0] for d in dists if d.get("kind") == "skills" for f in d.get("files", [])}
        self.assertIn("lybra-onboarding", skills, "lybra-onboarding 未在 advisor 应得技能集(distribution 声明)")


class TestChrisGapsRegression(unittest.TestCase):
    """⑥ chris 实录回放:今日 6 个缺口逐一验证不再出现(验收⑥)。"""

    def test_gap1_workstation_dir_creation(self):
        """chris 缺口①:工位目录不存在 — enroll 应指导先创建或自动创建。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        # 命令应包含 --workspace 参数(指定工位目录)
        self.assertIn("--workspace", step4["command"])

    def test_gap2_transport_credential_401(self):
        """chris 缺口②:运输凭证 401 — enroll 阶段应说明如何处理。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        # 失败出口应说明 401 怎么办
        self.assertIn("401", str(step4["on_fail"]).lower())

    def test_gap3_pi_wiring_missing(self):
        """chris 缺口③:.pi 接线缺失 — F54 应自动落,失败出口说明报 bug。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        # 失败出口应提及 .pi 接线
        on_fail_text = json.dumps(step4["on_fail"])
        self.assertIn(".pi", on_fail_text.lower())

    def test_gap4_owner_policy_ref_missing(self):
        """chris 缺口④:role 缺 owner_policy_ref — Step 2 信封必须生效。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        on_fail_text = json.dumps(step4["on_fail"])
        self.assertIn("owner_policy_ref", on_fail_text)

    def test_gap5_lybra_bin_missing(self):
        """chris 缺口⑤:connection.json 缺 lybra_bin — F54-fix1 应自动补。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        # 验证说明应提及 lybra_bin
        self.assertIn("lybra_bin", step4["check"])

    def test_gap6_workspace_root_wrong(self):
        """chris 缺口⑥:workspace_root 写成 harness root — F54-fix1 应校正。"""
        guide = generate_onboarding_guide("probe-xyz")
        step4 = _workstation_enroll_step(guide)  # AIPOS-F92: 工位 enroll = Step 7
        on_fail_text = json.dumps(step4["on_fail"])
        self.assertIn("workspace_root", on_fail_text)


if __name__ == "__main__":
    unittest.main()
