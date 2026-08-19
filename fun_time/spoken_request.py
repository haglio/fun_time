"""Which free transcriptions belong to a spoken request, and which do not.

"Request … over" is the one thing said in this room that is not a fixed
phrase.  Every other command is: the recognizer runs a restricted grammar and
can only ever answer with a phrase already on its list, which is what makes it
accurate enough to obey.  A request's words are whatever the speaker wants —
"no feet", "silver earrings" — so no list can hold them, and they arrive
instead on the free recognizer that runs alongside the grammar one on the same
audio (:func:`fun_time.voice_control.interpret_recognition`), whose
transcription otherwise only ever captions an unrecognized command.

So this decides which of those transcriptions to forward to the hosted
Origenerator, and when to stop.  Only that.  The request itself is assembled at
the far end by the hosted app's own dictation, which owns the markers, the
tolerance for a mangled one, and what a finished request does about the picture
it was said over — every utterance is forwarded verbatim, markers included, so
that assembly reads exactly what was said.

The half that has to be right here is the stopping.  A room that went on
forwarding after a missed "over" would post everything said for the rest of the
session to a slideshow, so an unterminated request is given up on after the
same handful of utterances the far end gives up on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# What opens a request, as the free recognizer renders it.  The far end allows
# a mis-heard letter in these; this deliberately does not.  Being the looser of
# the two would open the swallowing window on a word the far end then refuses,
# which eats the next several things said to the room and answers none of them.
_LEAD_WORDS = ("request", "requests", "requested")

# What closes one — matched only as an utterance's LAST word, since "over" is an
# ordinary word in the middle of a sentence ("the blanket over her legs") and a
# deliberate sign-off at the end of one.
_END_WORDS = ("over",)

# The sides a request can be aimed at, said before the lead word.  Bare, it
# rides the same "active" scope every other spoken phrase does, so it reaches
# whichever region was last addressed.
_SIDES = ("portrait", "landscape")

# How many utterances one request may run to before it is abandoned.  The far
# end's own cap, for the same reason: without one, a missed terminator holds
# the room hostage with nothing on screen to say why.
_MAX_UTTERANCES = 8


@dataclass(frozen=True)
class RelayedUtterance:
    """One utterance of a request, on its way to the hosted app.

    ``scope`` is the side it named, or ``"active"`` when it named none — the
    same scope word every other spoken phrase uses, resolved onto the region
    last addressed by the dispatch loop rather than here.  ``words`` is the
    transcription with that side word removed, and nothing else changed.
    """

    scope: str
    words: str


def _words(text: str) -> list[str]:
    """The lowercase words of a transcription, punctuation dropped."""
    return re.findall(r"[a-z']+", (text or "").lower())


class SpokenRequestRelay:
    """Tracks whether the room is part-way through saying a request.

    :meth:`push` takes one free transcription and answers with the utterance to
    forward, or ``None`` when it is not part of a request at all.
    """

    def __init__(self, *, max_utterances: int = _MAX_UTTERANCES) -> None:
        self._max = max_utterances
        self._scope: str | None = None
        self._heard = 0

    def reset(self) -> None:
        """Forget a half-said request.  Leaving origenerator mode calls this:
        one nobody can finish saying must not be waiting the next time the
        hosted app comes up."""
        self._scope = None
        self._heard = 0

    def push(self, text: str) -> RelayedUtterance | None:
        """Feed one free transcription; see the class docstring."""
        words = _words(text)
        if not words:
            return None
        if self._scope is None:
            return self._maybe_open(words, text)
        return self._continue(words, text)

    def _maybe_open(self, words: list[str], text: str) -> RelayedUtterance | None:
        scope = words[0] if words[0] in _SIDES else None
        lead = words[1:] if scope else words
        if not lead or lead[0] not in _LEAD_WORDS:
            return None  # not a request — the caller's other uses may have it
        self._scope = scope or "active"
        self._heard = 1
        body = _strip_leading_side(text) if scope else text
        # "Request, no feet, over" is a whole request in one breath, so the
        # opening utterance closes it like any other.
        if _ends_the_request(lead):
            scope_said = self._scope
            self.reset()
            return RelayedUtterance(scope_said, body)
        return RelayedUtterance(self._scope, body)

    def _continue(self, words: list[str], text: str) -> RelayedUtterance:
        self._heard += 1
        scope = self._scope
        if _ends_the_request(words) or self._heard >= self._max:
            # An abandoned request is still forwarded: the far end counts the
            # same utterances and gives up on the same one, and it is the end
            # that has a screen to say so on.
            self.reset()
        return RelayedUtterance(scope, text)


def _ends_the_request(words: list[str]) -> bool:
    """Whether an utterance's last word closes the request."""
    return bool(words) and words[-1] in _END_WORDS


def _strip_leading_side(text: str) -> str:
    """``text`` without its leading side word — the far end is told the side by
    the verb the words ride on, and would read a second one as part of the
    request."""
    return re.sub(r"^\W*[\w']+\W*", "", text or "", count=1)
