"""AIPOS-338 S5 — dispatch_mode (workspace-level auto|manual switch)."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.aipos_cli.pump_orchestration import DispatchContext, run_pump_dispatch
from tools.aipos_cli.workspace_config import (
    default_dispatch_mode,
    get_dispatch_mode,
    set_dispatch_mode,
    write_project_json,
)


def _make_project(tmp: Path) -> Path:
    root = tmp / "proj"
    (root / "5_tasks" / "queue" / "pending").mkdir(parents=True)
    write_project_json(root, "proj")
    return root


class TestDispatchModeStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_auto_when_absent(self):
        self.assertEqual(get_dispatch_mode(self.root), "auto")
        self.assertEqual(default_dispatch_mode(), "auto")

    def test_set_to_manual_writes_field_and_preserves_others(self):
        before = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        before["collaboration_profile"] = {"code_enabled": True}
        (self.root / "project.json").write_text(json.dumps(before), encoding="utf-8")
        mode, trail = set_dispatch_mode(self.root, "manual", by="owner", reason="pump failures")
        self.assertEqual(mode, "manual")
        self.assertEqual(get_dispatch_mode(self.root), "manual")
        # collaboration_profile preserved (read-modify-write, not schema-clobber)
        after = json.loads((self.root / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(after["collaboration_profile"], {"code_enabled": True})
        self.assertEqual(after["dispatch_mode"], "manual")
        # append-only trail written
        self.assertTrue(trail.is_file())
        log = trail.read_text(encoding="utf-8")
        self.assertIn("auto", log)
        self.assertIn("manual", log)
        self.assertIn("pump failures", log)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            set_dispatch_mode(self.root, "paused")

    def test_set_back_to_auto_appends_second_entry(self):
        set_dispatch_mode(self.root, "manual", by="owner", reason="downgrade")
        _, trail = set_dispatch_mode(self.root, "auto", by="owner", reason="recovered")
        log = trail.read_text(encoding="utf-8")
        # two transition lines
        self.assertEqual(log.count("->"), 2)


class TestPumpModeGate(unittest.TestCase):
    """manual mode refuses auto-dispatch; auto leaves manual /claim untouched."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = _make_project(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _ctx(self):
        return DispatchContext(
            card_id="AIPOS-9", role="executor",
            workspace_root=self.root, product_repo=self.root,
        )

    def test_manual_mode_refuses_dispatch_with_clear_message(self):
        set_dispatch_mode(self.root, "manual", reason="test")
        result = run_pump_dispatch(self._ctx(), dry_run=False, do_claim=False, do_launch=False, do_watch=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["step"], "dispatch_mode")
        self.assertTrue(result["errors"], "must refuse with a message")
        self.assertIn("manual", result["errors"][0])
        # message points the operator to manual /claim or switching back
        self.assertTrue(any("auto" in e or "/claim" in e for e in result["errors"]))

    def test_manual_mode_dry_run_previews_but_warns(self):
        set_dispatch_mode(self.root, "manual", reason="test")
        result = run_pump_dispatch(self._ctx(), dry_run=True, do_claim=False, do_launch=False, do_watch=False)
        # dry_run does not hard-fail on mode (it previews); it warns about manual
        warned = any("manual" in w for w in result.get("warnings", []))
        self.assertTrue(warned, "dry-run must warn that dispatch will be refused in manual")

    def test_auto_mode_does_not_block(self):
        # default auto: dispatch proceeds past the mode gate (it fails later on
        # missing kickoff inputs, NOT on dispatch_mode)
        result = run_pump_dispatch(self._ctx(), dry_run=False, do_claim=False, do_launch=False, do_watch=False)
        self.assertNotEqual(result.get("step"), "dispatch_mode")


if __name__ == "__main__":
    unittest.main()
