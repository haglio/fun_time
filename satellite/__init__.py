"""Fun Time's satellite program: a Funestra on a borderless window.

Launched as ``python -m satellite`` by :mod:`fun_time.windows_bridge_startup`,
one process per side, it opens the window the session places and runs a
:class:`player_core.funestra.Funestra` on it, driven entirely through the files
in ``state/``: a playlist, a command file, a paused flag, the published panel,
and a status file it writes back.
"""
