"""AIPOS-316 S1: CLI entry guard for internal modules.

This module provides a reusable guard to prevent direct invocation (python -m) of
internal modules that have no CLI contract. All tools/aipos_cli/*.py modules WITHOUT
a __main__ block should call check_direct_invocation() at module level to fail fast
with a clear error message and non-zero exit code.

Red line: this is a DEFENSIVE check, not a new feature. Zero new dependencies, zero
impact on normal import paths (the check only fires when __name__ == '__main__').
"""
import sys


def check_direct_invocation(module_name: str) -> None:
    """Guard against direct invocation of internal modules.
    
    Args:
        module_name: The __name__ of the calling module (pass __name__ literally).
    
    If the module is being run as __main__ (python -m tools.aipos_cli.foo), print
    an error message and exit with code 1. Otherwise (normal import), this is a no-op.
    
    Example usage (at module level in any internal module):
        from tools.aipos_cli._cli_entry_guard import check_direct_invocation
        check_direct_invocation(__name__)
    """
    if module_name == "__main__":
        # Infer the module path from the call stack (best effort)
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_file = frame.f_back.f_code.co_filename
            # Extract module path hint from file path
            if "tools/aipos_cli/" in caller_file:
                module_hint = caller_file.split("tools/aipos_cli/")[-1].replace(".py", "")
                module_hint = f"tools.aipos_cli.{module_hint}"
            else:
                module_hint = "this module"
        else:
            module_hint = "this module"
        
        print(
            f"Error: {module_hint} is not a command-line entry point.\n"
            f"This is an internal module with no CLI contract.\n"
            f"\n"
            f"Use the 'lybra' command instead. Examples:\n"
            f"  lybra agent watch --workspace-root <path>\n"
            f"  lybra agent launch-check --actor <name>\n"
            f"  lybra validate\n"
            f"\n"
            f"Run 'lybra --help' to see all available commands.",
            file=sys.stderr,
        )
        sys.exit(1)


# AIPOS-316 S1: Auto-guard for modules that import this but forget to call check_direct_invocation.
# This import-time check runs for ANY module that imports _cli_entry_guard and is itself run as __main__.
# It's a safety net, not a replacement for explicit check_direct_invocation() calls.
import inspect as _inspect
_calling_frame = _inspect.currentframe()
if _calling_frame and _calling_frame.f_back:
    _caller_name = _calling_frame.f_back.f_globals.get('__name__')
    if _caller_name == '__main__':
        # The importing module is being run as __main__ - guard it
        check_direct_invocation(_caller_name)
del _calling_frame, _inspect
