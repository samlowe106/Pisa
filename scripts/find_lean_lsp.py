#!/usr/bin/env python3
"""
Helper script to find and test Lean LSP command.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _resolve_candidate(cmd_str: str) -> str | None:
    """The executable path for ``cmd_str``'s command word, if it exists on disk (full path
    candidates) or in PATH (bare command candidates), else ``None``."""
    exe = cmd_str.split(maxsplit=1)[0]
    if exe.startswith(("/", "~")):
        exe_path = str(Path(exe).expanduser())
        if Path(exe_path).is_file() and os.access(exe_path, os.X_OK):
            return exe_path
        return None
    return shutil.which(exe)


def _looks_like_lsp(exe_path: str) -> bool:
    """Run ``exe_path --help`` and check the output for signs of LSP/server support."""
    try:
        result = subprocess.run(
            [exe_path, "--help"], capture_output=True, text=True, timeout=5, check=False
        )
    except Exception as e:  # noqa: BLE001 - best-effort probe, reported and moved on
        print(f"  Help check failed: {e}")
        return False
    if "--server" in result.stdout or "language" in result.stdout.lower():
        print("  Looks like LSP support!")
        return True
    return False


def find_lean_lsp() -> str | None:
    """Try to find Lean LSP in various locations."""
    candidates = [
        "lean --server",
        "lean-language-server",
        str(Path("~/.elan/bin/lean --server").expanduser()),
    ]

    # Check elan toolchain
    try:
        result = subprocess.run(
            ["elan", "show"], capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0:
            # elan is installed; try to get the toolchain
            print("elan is installed:", result.stdout.strip())
            candidates.append("lean --server")  # Will use elan's lean
    except Exception as e:  # noqa: BLE001 - best-effort probe, reported and moved on
        print(f"elan check failed: {e}")

    for cmd_str in candidates:
        exe_path = _resolve_candidate(cmd_str)
        if exe_path is None:
            print(f"Not found: {cmd_str.split()[0]}")
            continue
        print(f"Found: {cmd_str}")
        if _looks_like_lsp(exe_path):
            return cmd_str

    return None


if __name__ == "__main__":
    print("Searching for Lean LSP...")
    lsp_cmd = find_lean_lsp()
    if lsp_cmd:
        print(f"\nUse this in .env: LEAN_LSP_CMD={lsp_cmd}")
        sys.exit(0)
    else:
        print(
            "\nCould not find Lean LSP. Please install Lean 4 or set LEAN_LSP_CMD explicitly."
        )
        sys.exit(1)
