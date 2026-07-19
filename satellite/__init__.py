"""Fun Time's native satellite media player.

The two satellites (portrait + landscape) were VLC instances driven over VLC's
HTTP interface; this package replaces them with an in-process mpv-backed player,
the same way Nau replaced the primary VLC.  It builds on
:class:`player_core.mpv_player.MpvPlayer` for GPU-decoded playback and owns its
playlist in Python, so navigation is deterministic and pausing is an in-process
flag the player simply obeys — no HTTP, no re-pause watchdog.

Launched as ``python -m satellite`` by :mod:`fun_time.windows_bridge_startup`,
one process per side, and driven entirely through the file quartet in
``state/``: a playlist, a command file, a paused flag, and a status file it
writes back.  It lived in the genau repo until it stopped needing to — the mpv
wrapper it reached for is now player_core, so Fun Time's player belongs with
Fun Time.
"""
