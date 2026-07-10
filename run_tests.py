#!/usr/bin/env python3
"""Run every headless test suite in tests/ (no instruments needed).

Usage: .venv/bin/python run_tests.py
Exit code 0 only if every suite passes. Hardware-in-the-loop scripts
live in bench/ and are NOT run here -- see BENCH_TEST.md.
"""
import glob
import os
import subprocess
import sys


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    suites = sorted(glob.glob(os.path.join(root, 'tests', 'test_*.py')))
    failed = []
    for path in suites:
        name = os.path.basename(path)
        result = subprocess.run([sys.executable, path], cwd=root,
                                capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()
        tail = lines[-1] if lines else '(no output)'
        if result.returncode == 0:
            print(f"ok   {name:28s} {tail}")
        else:
            print(f"FAIL {name}\n{result.stdout}\n{result.stderr}")
            failed.append(name)
    print(f"\n{len(suites) - len(failed)}/{len(suites)} suites passed")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
