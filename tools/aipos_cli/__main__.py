"""AIPOS-290 S3: python -m tools.aipos_cli entry point (zero regression)."""
from tools.aipos_cli.aipos_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
