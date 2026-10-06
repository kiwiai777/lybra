"""AIPOS-F116 件②(gap #56): 本目录测试的会话层 HOME 隔离——直接跑 pytest 也不借真实 ~/.lybra / LYBRA_HOME_ROOT 解析到真实治理根、
工位与门凭据(run-all 执行器已隔离时不动)。唯一实现 tools.aipos_cli.runall_discovery.isolate_test_session(与执行器子进程同一环境构造)。"""
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.aipos_cli.runall_discovery import isolate_test_session  # noqa: E402

isolate_test_session()
