#!/usr/bin/env python3
"""Single source of truth for the application version.

Bump ``__version__`` on each release. ``version_string()`` appends the short
git commit hash when run from a checkout, so the footer pins the exact build
the user is running (useful when running from a feature branch).
"""

__version__ = "0.9.0"


def version_string():
    """Return ``v<version>`` plus the short git hash if available.

    Best-effort: if git is missing or this is not a checkout, just the
    semantic version is returned.
    """
    text = f"v{__version__}"
    try:
        import os
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=here, capture_output=True, text=True, timeout=1,
        )
        commit = result.stdout.strip()
        if result.returncode == 0 and commit:
            text += f"+{commit}"
    except Exception:
        pass
    return text
