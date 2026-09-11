"""The kinds a video can be — the vocabulary Evolver writes and the main player reads.

Four kinds, mutually exclusive, recorded on every library video's metadata
sidecar as ``video.type`` (Evolver's ``util/video_type.py`` is what writes
them).  They replace the several tests this player used to run to answer the
same question: a running time against a threshold of its own, the folder the
loops are delivered to, and the presence of a ``clip`` record.

The words live here rather than beside either the reader or the filter, because
both need them and neither owns them.
"""

from __future__ import annotations

GENAU_CLIP = "genau_clip"
EXCERPT = "excerpt"
SHORT = "short"
FULL_LENGTH = "full_length"
