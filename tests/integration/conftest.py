"""Force the real Windows platform for the integration suite.

Integration tests launch the real bridge and inspect real native windows (the
dashboard, Nau, VLC), so they must run on the native Qt platform — never the
offscreen platform the unit suite defaults to. The root ``tests/conftest.py`` sets
``QT_QPA_PLATFORM=offscreen`` so routine unit runs don't flash windows; because it is
an ancestor conftest it is imported first, so by the time this module runs the variable
is already ``"offscreen"``.

Remove it here — before any ``QApplication`` is created or any bridge/Nau subprocess is
spawned — so Qt falls back to the native windows platform and every child process the
integration session launches inherits a real platform too. Without this, those windows
would render offscreen and the Win32 inspection helpers would find nothing.
"""
from __future__ import annotations

import os

os.environ.pop("QT_QPA_PLATFORM", None)
