"""The AppUserModelID this session's processes and its pinned shortcut carry.

Windows groups a window under a pinned shortcut only when the two agree on one,
so this is the one spelling both halves read — and one id covers both shapes of
the session (docs/entering-vr.md).
"""
from __future__ import annotations

# Must match the value stamped on the pinned taskbar shortcut.
APP_USER_MODEL_ID = "FunTime.App"
