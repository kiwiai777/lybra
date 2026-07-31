"""Tests for direction_log migration tool and new directory structure."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aipos_cli.migrate_direction_log import (
    migrate_file,
    parse_monthly_file,
    slugify,
)
from tools.aipos_cli.project_map import _read_direction_log_recent


SAMPLE_MONTHLY_FILE = """# Lybra 方向层决策批量入册(2026-07-08 ~ 2026-07-11)

**性质说明**:本文档收录的是**方向层决策**——内容照录。

后续每轮方向收账,默认在本文件追加新日期段。

---

## 2026-07-07 — Agent 连接器进入 v1.0-REQUIRED

**决策**:agent 连接器升级为硬性要求。

**理由**:产品体验。

---

## 2026-07-09 — Planner 改道:BYO 外接 planner agent

**决策**:v1.0 的规划路径改为外接 planner agent。

---

## 2026-07-09(a) — Agent 连接器交付形态

**决策**:连接器交付形态定为 skills/ 目录。

---
"""


SAMPLE_DUAL_DATE_FILE = """# Lybra 方向层决策批量入册(2026-07-09 ~ 2026-07-10)

**性质说明**:本文档收录的是**方向层决策**——内容照录。

---

## 2026-07-09 — Planner 改道:BYO 外接 planner agent

**决策**:v1.0 的规划路径改为外接 planner agent。

**理由**:分离关注点。

---

## 2026-07-09 / 2026-07-10 — Homerail 两轮分析与借鉴清单

**决策**:对 homerail(`xiaotianfotos/homerail`,家庭 NAS DAG 编排 runtime)做了两轮分析,
产出定位反证 + 分层借鉴清单。

**第一轮结论(定位层)**:homerail ROADMAP 原文要义——"它明确不做软件工程,只选'结果
一眼能判'的产出(视频/报告/资产),因为软件是人最难评判的产出"。

**实现状态**:第 1 条已落地(见上);其余方向性记录,尚未出 DRAFT。

---

## 2026-07-10 — 门户项目真实案例 + 主 project 角色红线子需求

**决策**:基于门户项目真实案例,明确主 project 角色红线。

---
"""


class MigrationToolTests(unittest.TestCase):
    def test_slugify(self) -> None:
        """Slugify converts titles to filesystem-safe names."""
        self.assertEqual(slugify("Agent 连接器进入 v1.0-REQUIRED"), 
                         "agent-连接器进入-v10-required")
        self.assertEqual(slugify("Planner 改道:BYO 外接 planner agent"),
                         "planner-改道byo-外接-planner-agent")
        self.assertEqual(slugify("Simple Title"), "simple-title")
        self.assertEqual(slugify("A" * 100), "a" * 50)  # Truncates

    def test_parse_monthly_file(self) -> None:
        """Parser extracts preamble and entries correctly."""
        preamble, entries = parse_monthly_file(SAMPLE_MONTHLY_FILE)
        
        # Preamble extracted
        self.assertIn("性质说明", preamble)
        self.assertIn("后续每轮", preamble)
        
        # Three entries found
        self.assertEqual(len(entries), 3)
        
        # First entry
        self.assertEqual(entries[0].date, "2026-07-07")
        self.assertEqual(entries[0].title, "Agent 连接器进入 v1.0-REQUIRED")
        self.assertIn("## 2026-07-07", entries[0].content)
        self.assertIn("产品体验", entries[0].content)
        
        # Entry with suffix
        self.assertEqual(entries[2].date, "2026-07-09")
        self.assertEqual(entries[2].title, "Agent 连接器交付形态")
        self.assertIn("## 2026-07-09(a)", entries[2].content)

    def test_migrate_file_dry_run(self) -> None:
        """Migration tool plans correct file structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "2026-07-direction-decisions.md"
            source.write_text(SAMPLE_MONTHLY_FILE, encoding="utf-8")
            
            target_base = Path(tmpdir) / "direction_log"
            result = migrate_file(source, target_base, dry_run=True)
            
            self.assertEqual(result["year_month"], "2026-07")
            self.assertEqual(result["entries_count"], 3)
            self.assertTrue(result["dry_run"])
            
            # Check planned actions
            actions = result["actions"]
            self.assertTrue(any("07-01-agent" in a for a in actions))
            self.assertTrue(any("09-01-planner" in a for a in actions))
            self.assertTrue(any("09-02-agent" in a for a in actions))
            self.assertTrue(any("INDEX.md" in a for a in actions))
            self.assertTrue(any(".archived" in a for a in actions))

    def test_migrate_file_real_write(self) -> None:
        """Migration writes files and archives original."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "2026-07-direction-decisions.md"
            source.write_text(SAMPLE_MONTHLY_FILE, encoding="utf-8")
            
            target_base = Path(tmpdir) / "direction_log"
            result = migrate_file(source, target_base, dry_run=False)
            
            self.assertEqual(result["entries_count"], 3)
            self.assertFalse(result["dry_run"])
            
            # Original archived
            self.assertFalse(source.exists())
            archived = Path(tmpdir) / "2026-07-direction-decisions.md.archived"
            self.assertTrue(archived.exists())
            
            # Month directory created
            month_dir = target_base / "2026-07"
            self.assertTrue(month_dir.is_dir())
            
            # Entry files exist
            entry1 = month_dir / "07-01-agent-连接器进入-v10-required.md"
            self.assertTrue(entry1.exists())
            content1 = entry1.read_text(encoding="utf-8")
            self.assertIn("## 2026-07-07", content1)
            self.assertIn("产品体验", content1)
            
            # Entry with suffix
            entry3 = month_dir / "09-02-agent-连接器交付形态.md"
            self.assertTrue(entry3.exists())
            content3 = entry3.read_text(encoding="utf-8")
            self.assertIn("## 2026-07-09(a)", content3)
            
            # INDEX.md exists
            index = month_dir / "INDEX.md"
            self.assertTrue(index.exists())
            index_content = index.read_text(encoding="utf-8")
            self.assertIn("# Direction Log Index — 2026-07", index_content)
            self.assertIn("[2026-07-07]", index_content)
            self.assertIn("[2026-07-09]", index_content)

    def test_parse_dual_date_heading(self) -> None:
        """Parser correctly handles dual-date range headings."""
        preamble, entries = parse_monthly_file(SAMPLE_DUAL_DATE_FILE)
        
        # Three entries found
        self.assertEqual(len(entries), 3)
        
        # Single date entry
        self.assertEqual(entries[0].date, "2026-07-09")
        self.assertEqual(entries[0].title, "Planner 改道:BYO 外接 planner agent")
        
        # Dual-date entry: uses first date for routing
        self.assertEqual(entries[1].date, "2026-07-09")
        self.assertEqual(entries[1].title, "Homerail 两轮分析与借鉴清单")
        self.assertIn("## 2026-07-09 / 2026-07-10", entries[1].content)
        self.assertIn("homerail", entries[1].content)
        
        # Normal single date after dual-date
        self.assertEqual(entries[2].date, "2026-07-10")
        self.assertEqual(entries[2].title, "门户项目真实案例 + 主 project 角色红线子需求")

    def test_migrate_dual_date_real_scenario(self) -> None:
        """Migration handles real .archived file scenario with dual-date heading."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "2026-07-direction-decisions.md"
            source.write_text(SAMPLE_DUAL_DATE_FILE, encoding="utf-8")
            
            target_base = Path(tmpdir) / "direction_log"
            result = migrate_file(source, target_base, dry_run=False)
            
            self.assertEqual(result["entries_count"], 3)
            
            month_dir = target_base / "2026-07"
            
            # Dual-date entry routed by first date (09)
            dual_date_file = month_dir / "09-02-homerail-两轮分析与借鉴清单.md"
            self.assertTrue(dual_date_file.exists())
            dual_content = dual_date_file.read_text(encoding="utf-8")
            self.assertIn("## 2026-07-09 / 2026-07-10", dual_content)
            self.assertIn("homerail", dual_content)
            
            # INDEX references dual-date entry
            index = month_dir / "INDEX.md"
            index_content = index.read_text(encoding="utf-8")
            self.assertIn("[2026-07-09](./09-02-homerail", index_content)
            self.assertIn("Homerail 两轮分析与借鉴清单", index_content)


class DirectionLogParsingTests(unittest.TestCase):
    """Test project_map.py reads both old and new structures."""
    
    def test_read_old_structure(self) -> None:
        """Parser reads old monthly aggregate files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = Path(tmpdir) / "governance"
            dl_dir = gov / "direction_log"
            dl_dir.mkdir(parents=True)
            
            # Old structure: single file with multiple entries
            (dl_dir / "2026-07-direction-decisions.md").write_text(
                "# DL\n\n"
                "## 2026-07-10 — Third\n\nBody 3\n\n"
                "## 2026-07-09 — Second\n\nBody 2\n\n"
                "## 2026-07-07 — First\n\nBody 1\n",
                encoding="utf-8"
            )
            
            entries = _read_direction_log_recent(gov, limit=3)
            
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0]["date"], "2026-07-10")
            self.assertEqual(entries[0]["title"], "Third")
            self.assertEqual(entries[1]["date"], "2026-07-09")
            self.assertEqual(entries[2]["date"], "2026-07-07")
    
    def test_read_new_structure(self) -> None:
        """Parser reads new directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = Path(tmpdir) / "governance"
            dl_dir = gov / "direction_log"
            month_dir = dl_dir / "2026-07"
            month_dir.mkdir(parents=True)
            
            # New structure: individual files
            (month_dir / "07-01-first.md").write_text(
                "## 2026-07-07 — First\n\nBody 1\n",
                encoding="utf-8"
            )
            (month_dir / "09-01-second.md").write_text(
                "## 2026-07-09 — Second\n\nBody 2\n",
                encoding="utf-8"
            )
            (month_dir / "10-01-third.md").write_text(
                "## 2026-07-10 — Third\n\nBody 3\n",
                encoding="utf-8"
            )
            (month_dir / "INDEX.md").write_text(
                "# Index\n\n- [2026-07-07](./07-01-first.md)\n",
                encoding="utf-8"
            )
            
            entries = _read_direction_log_recent(gov, limit=3)
            
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0]["date"], "2026-07-10")
            self.assertEqual(entries[0]["title"], "Third")
            self.assertEqual(entries[1]["date"], "2026-07-09")
            self.assertEqual(entries[2]["date"], "2026-07-07")
    
    def test_read_new_structure_with_suffix(self) -> None:
        """Parser handles date suffixes in new structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = Path(tmpdir) / "governance"
            dl_dir = gov / "direction_log"
            month_dir = dl_dir / "2026-07"
            month_dir.mkdir(parents=True)
            
            (month_dir / "09-01-first.md").write_text(
                "## 2026-07-09 — First\n\nBody 1\n",
                encoding="utf-8"
            )
            (month_dir / "09-02-second.md").write_text(
                "## 2026-07-09(a) — Second with suffix\n\nBody 2\n",
                encoding="utf-8"
            )
            
            entries = _read_direction_log_recent(gov, limit=5)
            
            self.assertEqual(len(entries), 2)
            # Both have same date but different titles
            dates = [e["date"] for e in entries]
            self.assertEqual(dates.count("2026-07-09"), 2)
            titles = [e["title"] for e in entries]
            self.assertIn("First", titles)
            self.assertIn("Second with suffix", titles)
    
    def test_read_multiple_months(self) -> None:
        """Parser aggregates across multiple month directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            gov = Path(tmpdir) / "governance"
            dl_dir = gov / "direction_log"
            
            june = dl_dir / "2026-06"
            june.mkdir(parents=True)
            (june / "25-01-june-entry.md").write_text(
                "## 2026-06-25 — June entry\n\nOld\n",
                encoding="utf-8"
            )
            
            july = dl_dir / "2026-07"
            july.mkdir(parents=True)
            (july / "10-01-july-entry.md").write_text(
                "## 2026-07-10 — July entry\n\nNew\n",
                encoding="utf-8"
            )
            
            entries = _read_direction_log_recent(gov, limit=5)
            
            self.assertEqual(len(entries), 2)
            # Sorted by date descending
            self.assertEqual(entries[0]["date"], "2026-07-10")
            self.assertEqual(entries[1]["date"], "2026-06-25")


if __name__ == "__main__":
    unittest.main()
