#!/usr/bin/env python3
"""
Test runner script for TiebaMecha.

Usage:
    python run_tests.py           # Run all tests
    python run_tests.py -v        # Verbose output
    python run_tests.py --cov     # With coverage report
    python run_tests.py tests/test_account.py  # Run specific file
"""

import subprocess
import sys
from pathlib import Path


def main():
    """Run pytest with the specified arguments."""
    # Ensure we're in the project root
    # 本脚本位于 scripts/developer/ 下：根目录是上两级（此前写成脚本所在目录，
    # 会在 scripts/developer 里找 tests/ 而一无所获——2026-11-24 体检）
    project_root = Path(__file__).resolve().parents[2]

    # Build pytest command
    cmd = [sys.executable, "-m", "pytest"]

    # Add arguments from command line
    args = sys.argv[1:]

    # If --cov flag is present, add coverage options
    if "--cov" in args:
        args.remove("--cov")
        cmd.extend(["--cov=src/tieba_mecha", "--cov-report=term-missing"])

    cmd.extend(args)

    # Default to tests directory if no specific path given
    if not any(arg.startswith("tests") or arg.endswith(".py") for arg in args):
        cmd.append("tests")

    print(f"Running: {' '.join(cmd)}")
    print("-" * 60)

    # Run pytest
    result = subprocess.run(cmd, cwd=project_root)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
