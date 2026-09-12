"""Reading and writing Windows .lnk shortcuts.

The resolver swallows every exception twice and hands back an empty shortcut,
which its caller turns into one "skipped" log line -- a failure mode nothing
louder can catch, so what CAN be pinned off Windows is pinned here.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

from fun_time.shortcuts import Shortcut, ps_quote, resolve_shortcut


class TestPsQuote:
    def test_wraps_a_plain_value(self):
        assert ps_quote(r"C:\dir\file.lnk") == r"'C:\dir\file.lnk'"

    def test_doubles_a_quote_so_it_cannot_end_the_literal(self):
        """A path may carry an apostrophe, and PowerShell would otherwise read
        the rest of it as code."""
        assert ps_quote("it's") == "'it''s'"


class TestResolveShortcut:
    """The .lnk resolver under the Random Favs Browser launch.

    It swallows every exception twice and hands back an empty shortcut, which the
    caller turns into one 'skipped' log line — a failure mode nothing louder
    can catch, so what CAN be pinned off Windows is pinned here: the
    PowerShell fallback's parsing, and the all-quiet dead end.  (The COM fast path is
    Windows-only flesh; the integration suite is its only cover.)
    """

    @staticmethod
    def _without_com(monkeypatch):
        """Force the win32com import to fail, as it does off Windows — and so
        the test means the same thing on Windows CI, where it would otherwise
        answer from real COM."""
        import sys as _sys

        monkeypatch.setitem(_sys.modules, "win32com", None)
        monkeypatch.setitem(_sys.modules, "win32com.client", None)

    def test_parses_the_three_fields_powershell_reports(self, monkeypatch):
        self._without_com(monkeypatch)
        completed = SimpleNamespace(
            stdout="C:\\Chrome\\chrome.exe\r\nC:\\Chrome\r\n--profile-directory=\"Profile 2\"\r\n",
            returncode=0,
        )
        with patch("fun_time.shortcuts.subprocess.run",
                   return_value=completed):
            resolved = resolve_shortcut(r"C:\fake\s.lnk")

        assert resolved == Shortcut(
            "C:\\Chrome\\chrome.exe", "C:\\Chrome", '--profile-directory="Profile 2"')

    def test_a_bare_target_resolves_without_workdir_or_args(self, monkeypatch):
        self._without_com(monkeypatch)
        completed = SimpleNamespace(stdout="C:\\Chrome\\chrome.exe\r\n", returncode=0)
        with patch("fun_time.shortcuts.subprocess.run",
                   return_value=completed):
            assert resolve_shortcut(r"C:\fake\s.lnk") == Shortcut(
                "C:\\Chrome\\chrome.exe", "", "")

    def test_every_resolver_failing_is_three_empty_strings_not_a_raise(self, monkeypatch):
        self._without_com(monkeypatch)
        with patch("fun_time.shortcuts.subprocess.run",
                   side_effect=OSError("no powershell")):
            assert resolve_shortcut(r"C:\fake\s.lnk") == Shortcut()


    def test_each_link_that_fails_says_so_before_the_next_one_is_tried(
            self, monkeypatch, caplog):
        """"Random Favs Browser skipped: could not resolve shortcut" was the
        whole account of a failure with two resolvers under it, so the one
        question worth asking — which link broke, and how — had no answer
        anywhere.  Each fall-through now says which resolver it was and what it
        raised, at debug, so the working case stays silent."""
        self._without_com(monkeypatch)
        with caplog.at_level(logging.DEBUG, logger="windows_bridge"), \
             patch("fun_time.shortcuts.subprocess.run",
                   side_effect=OSError("no powershell")):
            assert resolve_shortcut(r"C:\fake\s.lnk") == Shortcut()

        said = " ".join(record.getMessage() for record in caplog.records)
        assert "COM" in said and "PowerShell" in said
        assert any(record.exc_info for record in caplog.records), (
            "the fall-through has to carry what was raised, or it explains nothing")
