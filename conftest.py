"""AIPOS-F121 件①(F116 发现 ④/缺口 6): 仓根会话层 HOME 隔离——覆盖全部测试目录(含 F116 各目录 conftest 覆盖不到的仓根、tools/、
tools/lybra_tui/tests、tools/sandbox_runtime/tests、task_cards/*): 直接跑 pytest 也不借真实 ~/.lybra / LYBRA_HOME_ROOT 解析到真实
治理根、工位与门凭据(run-all 执行器已隔离时不动)。唯一实现 tools.aipos_cli.runall_discovery.isolate_test_session(F116;
与执行器子进程同一环境构造), 本文件不另写环境构造。各目录 conftest 再调一次为空操作(已隔离标记 == HOME)。"""
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.aipos_cli.runall_discovery import isolate_test_session  # noqa: E402

isolate_test_session()
