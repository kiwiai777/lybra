"""Read-only AI Project OS CLI package."""
# AIPOS-F263 件①: 解释器过旧 = 可读提示 + 退出码 1(早于任何用到新语法的模块被编译; 版本要求读 config.schema setup_check)
from tools.aipos_cli._python_guard import require_python as _require_python

_require_python()
