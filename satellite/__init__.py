"""Fun Time's native satellite media player.

Built on :class:`player_core.mpv_player.MpvPlayer` and owning its playlist in
Python, so navigation is deterministic and pausing is an in-process flag the
player simply obeys.  Launched as ``python -m satellite`` by
:mod:`fun_time.windows_bridge_startup`, one process per side, and driven
entirely through the file quartet in ``state/``: a playlist, a command file, a
paused flag, and a status file it writes back.
"""
