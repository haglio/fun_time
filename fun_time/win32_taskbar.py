"""The AppUserModelID this session's processes and its pinned shortcut carry.

Windows groups a running window under a pinned taskbar shortcut only when the
two agree on one: the process claims it before opening any window
(``app_support.win32.set_app_user_model_id``), and the shortcut carries the same
string in its ``System.AppUserModel.ID`` (``set_shortcut_app_user_model_id``
puts it there).  This is the one spelling both halves read.

One id for both shapes of the session — one app, one button, since the headset
stopped being a separate thing to start (docs/entering-vr.md).
"""
from __future__ import annotations

# Must match the value stamped on the pinned taskbar shortcut.
APP_USER_MODEL_ID = "FunTime.App"
