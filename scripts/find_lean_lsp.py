#!/usr/bin/env python3
"""
Helper script to find and test Lean LSP command.
"""

import subprocess
import shutil
import sys
import os


def find_lean_lsp():
    """Try to find Lean LSP in various locations."""
    candidates = [
        "lean --server",
        "lean-language-server",
        os.path.expanduser("~/.elan/bin/lean --server"),
    ]

    # Check elan toolchain
    try:
        result = subprocess.run(
            ["elan", "show"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # elan is installed; try to get the toolchain
            print("elan is installed:", result.stdout.strip())
            candidates.append("lean --server")  # Will use elan's lean
    except Exception as e:
        print(f"elan check failed: {e}")

    for cmd_str in candidates:
        parts = cmd_str.split()
        exe = parts[0]

        # Handle full paths
        if exe.startswith("/") or exe.startswith("~"):
            exe_to_check = os.path.expanduser(exe)
            if os.path.isfile(exe_to_check) and os.access(exe_to_check, os.X_OK):
                print(f"Found: {cmd_str}")
                try:
                    result = subprocess.run(
                        [exe_to_check, "--help"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if (
                        "--server" in result.stdout
                        or "language" in result.stdout.lower()
                    ):
                        print("  Looks like LSP support!")
                        return cmd_str
                except Exception as e:
                    print(f"  Help check failed: {e}")
        else:
            # Check in PATH
            if shutil.which(exe):
                print(f"Found: {cmd_str}")
                try:
                    result = subprocess.run(
                        [exe, "--help"], capture_output=True, text=True, timeout=5
                    )
                    if (
                        "--server" in result.stdout
                        or "language" in result.stdout.lower()
                    ):
                        print("  Looks like LSP support!")
                        return cmd_str
                except Exception:
                    pass
            else:
                print(f"Not found: {exe}")

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
