"""Tests for the spoken-request relay: which free transcriptions to forward.

The words of a request are the speaker's own, so none of them can be in the
recognizer's grammar; what this half decides is only which transcriptions
belong to a request and when to stop forwarding.  The request itself is
assembled at the far end, so every utterance goes on verbatim, markers and all.

Fabricated request wording throughout — never anything lifted from the library.
"""
from __future__ import annotations

from fun_time.spoken_request import SpokenRequestRelay


class TestOpeningARequest:
    def test_a_side_and_the_lead_word_open_one(self):
        relay = SpokenRequestRelay()
        relayed = relay.push("landscape request no feet")
        assert relayed is not None
        # The side rides the command, so the words go on without it — a second
        # "landscape" would be read as part of the request itself.
        assert (relayed.scope, relayed.words) == ("landscape", "request no feet")

    def test_no_side_rides_the_active_scope_like_every_other_bare_phrase(self):
        relay = SpokenRequestRelay()
        relayed = relay.push("request no feet")
        assert (relayed.scope, relayed.words) == ("active", "request no feet")

    def test_speech_that_is_not_a_request_is_left_alone(self):
        """Everything else stays the caller's: an unrecognized phrase still has
        to reach the "unrecognized voice command" flash."""
        relay = SpokenRequestRelay()
        assert relay.push("full length please") is None
        assert relay.push("landscape full length") is None

    def test_the_word_has_to_lead(self):
        """A sentence merely containing it is not one — that is far likelier to
        be an ordinary mis-hearing, and opening on it swallows what follows."""
        relay = SpokenRequestRelay()
        assert relay.push("i would request that") is None
        # …and nothing after it is swallowed, which is what opening would do.
        assert relay.push("landscape next") is None

    def test_a_whole_request_in_one_breath_opens_and_closes(self):
        relay = SpokenRequestRelay()
        relayed = relay.push("portrait request no feet over")
        assert (relayed.scope, relayed.words) == ("portrait", "request no feet over")
        assert relay.push("landscape next") is None  # closed: the next word is free again


class TestCollectingARequest:
    def test_every_utterance_goes_on_until_the_terminator(self):
        """The pauses are the speaker's, and each one ends an utterance — so a
        sentence said in three arrives in three, and all three are forwarded on
        the side the first one named."""
        relay = SpokenRequestRelay()
        said = [
            relay.push("landscape request no feet"),
            relay.push("and a longer skirt"),
            relay.push("over"),
        ]
        assert [(r.scope, r.words) for r in said] == [
            ("landscape", "request no feet"),
            ("landscape", "and a longer skirt"),
            ("landscape", "over"),
        ]
        assert relay.push("landscape next") is None  # closed: the next word is free again

    def test_a_command_shaped_utterance_mid_request_is_still_the_request(self):
        """Half a sentence must not fire a command because two of its words
        happened to be one — the request has first refusal while it is open,
        exactly as it does on the hosted app's own mic."""
        relay = SpokenRequestRelay()
        relay.push("request no feet")
        relayed = relay.push("landscape next")
        assert (relayed.scope, relayed.words) == ("active", "landscape next")

    def test_only_a_trailing_over_closes_it(self):
        """"over" is an ordinary word in the middle of a sentence and a
        deliberate sign-off at the end of one."""
        relay = SpokenRequestRelay()
        relay.push("request no feet")
        assert relay.push("the blanket over her legs") is not None  # still collecting
        relay.push("over")
        assert relay.push("landscape next") is None

    def test_an_unterminated_request_is_given_up_on(self):
        """Without a cap a missed "over" would post everything said for the rest
        of the session to a slideshow, with nothing on screen to say why."""
        relay = SpokenRequestRelay(max_utterances=3)
        relay.push("request no feet")
        assert relay.push("and a longer skirt") is not None  # still collecting
        relay.push("still going")
        # …and the next thing said is free to be a command again.
        assert relay.push("landscape next") is None

    def test_reset_drops_a_half_said_request(self):
        """Leaving origenerator mode calls this: one nobody can finish saying
        must not still be collecting when the mode comes back."""
        relay = SpokenRequestRelay()
        relay.push("request no feet")
        relay.reset()
        assert relay.push("and a longer skirt") is None
