#!/usr/bin/env python3
"""AIPOS-F41 验收测试:同源生成验证。

验证①:改手册硬规矩节的一条→章程分发产物与新派生审计卡的注入段同步跟随。

测试策略:
1. 从顾问手册提取硬规矩
2. 验证三个角色章程都包含硬规矩节
3. 验证提取器工作正常
"""
import sys
from pathlib import Path

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import json
import tempfile

from tools.aipos_cli.hard_rules_extractor import (
    extract_hard_rules_from_handbook,
    extract_diagnostic_checklist_from_handbook,
    hard_rules_source_ref,
)

# 产品仓根 = 测试文件所在 tests/ 的父目录(可移植,禁硬编码绝对路径)
_PRODUCT_REPO = Path(__file__).resolve().parent.parent

# AIPOS-F89 件② M17: 硬规矩来源 = 项目声明 project.json paths.hard_rules_source(产品不再假设任何治理文档名)。
# 本夹具不再读真实治理根(原读 lybra 自家命令手册 = 项目假设), 改为靶场项目: 声明一份手册(节结构 = 提取器契约) / 不声明。
_HANDBOOK = """# 项目手册

## 0.5. 硬规矩

1. **永不接触门** — 不调用门动词。
2. **凭据只按名引用** — 不回显密钥。
3. **门领地只读** — 记录由门落盘。
4. **报错即停** — 不自行修复。
5. **写完报告即停** — 账务由产品做。
6. **拒后禁换方式重试** — 如实上报。

**实撞背景**:靶场背景段。

## 6.9 阻塞分诊与排查路径

**链条卡点三查**:先查队列, 再查记录, 后查产物。

| 症状 | 定位 |
|---|---|
| 卡不动 | 查推导核 |

**escalation 路径**:停线上报。

## 7. 其他
"""


def _project(declared: bool) -> Path:
    root = Path(tempfile.mkdtemp(prefix="f41-gov-"))
    decl = {"project": "f41", "config_version": 1}
    if declared:
        (root / "docs").mkdir()
        (root / "docs" / "handbook.md").write_text(_HANDBOOK, encoding="utf-8")
        decl["paths"] = {"hard_rules_source": "docs/handbook.md"}
    (root / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    return root


def test_hard_rules_extraction():
    """测试从项目声明的硬规矩来源提取硬规矩; 未声明 = 跳过(ok=False, skipped, 明确拒因), 不读任何默认文档名。"""
    gov_root = _project(declared=True)
    result = extract_hard_rules_from_handbook(gov_root)

    print("=" * 80)
    print("测试1: 硬规矩提取")
    print("=" * 80)
    print(f"提取成功: {result['ok']}")
    print(f"找到节: {result['section_found']}")
    print(f"规矩条数: {len(result['rules_list'])}")
    
    if result['ok']:
        print("\n提取的规矩:")
        for i, rule in enumerate(result['rules_list'], 1):
            print(f"  {i}. {rule[:80]}...")
    else:
        print(f"错误: {result['error']}")
    
    assert result['ok'], f"硬规矩提取失败: {result['error']}"
    assert result['section_found'], "未找到硬规矩节"
    assert len(result['rules_list']) == 6, f"应有6条规矩,实际{len(result['rules_list'])}条"
    assert hard_rules_source_ref(gov_root) == "docs/handbook.md § 0.5"

    undeclared = extract_hard_rules_from_handbook(_project(declared=False))
    print(f"未声明: ok={undeclared['ok']} skipped={undeclared.get('skipped')} error={undeclared['error']}")
    assert undeclared["ok"] is False and undeclared.get("skipped") is True
    assert undeclared["error"].startswith("HARD_RULES_SOURCE_UNDECLARED")
    
    print("\n✓ 硬规矩提取测试通过")
    return True


def test_diagnostic_checklist_extraction():
    """测试从项目声明的手册提取诊断清单。"""
    gov_root = _project(declared=True)
    result = extract_diagnostic_checklist_from_handbook(gov_root)
    
    print("\n" + "=" * 80)
    print("测试2: 诊断清单提取")
    print("=" * 80)
    print(f"提取成功: {result['ok']}")
    print(f"找到节: {result['section_found']}")
    
    if result['ok']:
        print(f"三查步骤: {'有' if result['三查步骤'] else '无'}")
        print(f"卡点对照表: {'有' if result['卡点对照表'] else '无'}")
        print(f"escalation路径: {'有' if result['escalation路径'] else '无'}")
    else:
        print(f"错误: {result['error']}")
    
    assert result['ok'], f"诊断清单提取失败: {result['error']}"
    assert result['section_found'], "未找到诊断清单节"
    assert extract_diagnostic_checklist_from_handbook(_project(declared=False)).get("skipped") is True
    
    print("\n✓ 诊断清单提取测试通过")
    return True


def test_charters_contain_hard_rules():
    """测试章程文件包含硬规矩节。"""
    print("\n" + "=" * 80)
    print("测试3: 章程包含硬规矩节")
    print("=" * 80)
    
    product_repo = _PRODUCT_REPO
    roles = ["executor", "auditor", "advisor"]
    
    for role in roles:
        charter = product_repo / "agents" / "roles" / role / "AGENTS.md"
        assert charter.is_file(), f"章程文件不存在: {charter}"
        
        content = charter.read_text(encoding="utf-8")
        
        # 检查是否包含硬规矩节标题
        has_hard_rules = "## 🟡 硬规矩" in content
        has_source_note = "单一真相源" in content
        has_six_rules = content.count("1. **永不") > 0 or content.count("1. **") > 0
        
        print(f"\n{role}:")
        print(f"  包含硬规矩节: {has_hard_rules}")
        print(f"  包含单一真相源注释: {has_source_note}")
        print(f"  包含规矩条目: {has_six_rules}")
        
        assert has_hard_rules, f"{role}章程缺少硬规矩节"
        assert has_source_note, f"{role}章程缺少单一真相源注释"
    
    print("\n✓ 章程硬规矩节测试通过")
    return True


def test_roles_schema_planner_scopes():
    """测试roles.schema的planner scopes包含新权限。"""
    print("\n" + "=" * 80)
    print("测试4: planner scopes包含新权限(B2)")
    print("=" * 80)
    
    import json
    product_repo = _PRODUCT_REPO
    schema_path = product_repo / "schema" / "roles.schema.json"
    
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    
    planner = next((r for r in schema["roles"] if r["role"] == "planner"), None)
    assert planner, "未找到planner角色"
    
    scopes = planner.get("scopes", [])
    has_withdraw = "queue_withdraw" in scopes
    has_claim = "queue_claim" in scopes
    
    print(f"planner scopes: {scopes}")
    print(f"包含queue_withdraw: {has_withdraw}")
    print(f"包含queue_claim: {has_claim}")
    
    assert has_withdraw, "planner缺少queue_withdraw权限"
    assert has_claim, "planner缺少queue_claim权限"
    
    print("\n✓ planner权限测试通过")
    return True


def main():
    """运行所有测试。"""
    print("AIPOS-F41 验收测试\n")
    
    try:
        test_hard_rules_extraction()
        test_diagnostic_checklist_extraction()
        test_charters_contain_hard_rules()
        test_roles_schema_planner_scopes()
        
        print("\n" + "=" * 80)
        print("✓ 所有测试通过")
        print("=" * 80)
        return 0
    except AssertionError as e:
        print(f"\n✗ 测试失败: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
