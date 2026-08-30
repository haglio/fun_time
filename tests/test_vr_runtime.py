"""fun_time_vr.vr_runtime — is VR ready, and can we bring it up ourselves?

Adapted from genau's proven suite for the module this one was adapted from
(``genau_vr.vr_runtime``); the differences under test are fun_time's own —
``xr`` imported lazily inside ``probe`` (stubbed through ``sys.modules``, not
a module attribute), and the FunTimeVR popup wording.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from fun_time_vr.vr_runtime import (
    Probe,
    Readiness,
    active_runtime_json,
    ensure_ready,
    explain,
    launcher_for_runtime,
    probe,
)


def _xr_exceptions():
    """The loader's exception hierarchy — real where the loader imports.

    On Windows (CI, the authority) this is pyopenxr's own module, so a rename
    there cannot pass silently.  On this Mac pyopenxr's loader refuses to
    import at all (NotImplementedError from xr.library), so stand-ins with
    the same names keep the platform-independent probe logic testable; the
    probe only ever meets these types through the stub module either way.
    """
    try:
        import xr  # noqa: PLC0415 — only the cases that need the loader should load it

        return xr.exception
    except Exception:
        return _FALLBACK_EXCEPTIONS


class _FALLBACK_EXCEPTIONS:  # one hierarchy, so raise and except meet the same classes
    class RuntimeUnavailableError(Exception):
        pass

    class FormFactorUnavailableError(Exception):
        pass


def _xr_stub(*, create_error=None, system_error=None) -> MagicMock:
    """A stand-in for the ``xr`` module that fails where we tell it to."""
    stub = MagicMock()
    stub.exception = _xr_exceptions()
    if create_error is not None:
        stub.create_instance.side_effect = create_error
    if system_error is not None:
        stub.get_system.side_effect = system_error
    return stub


def _with_xr(stub):
    """``probe`` imports xr lazily, so the stub goes in through sys.modules."""
    return patch.dict(sys.modules, {"xr": stub})


def _pimax_tree(root, *, with_client: bool = True):
    """Lay out a Pimax install the way the real one sits on disk."""
    runtime_json = root / "Pimax" / "Runtime" / "PiOpenXR_64.json"
    runtime_json.parent.mkdir(parents=True)
    runtime_json.write_text("{}", encoding="utf-8")
    client = root / "Pimax" / "PimaxClient" / "pimaxui" / "PimaxClient.exe"
    if with_client:
        client.parent.mkdir(parents=True)
        client.write_text("", encoding="utf-8")
    return runtime_json, client


def test_probe_reports_ready_when_a_headset_answers():
    with _with_xr(_xr_stub()):
        assert probe().readiness is Readiness.READY


def test_probe_reports_no_runtime_when_the_loader_finds_none():
    error = _xr_exceptions().RuntimeUnavailableError()
    with _with_xr(_xr_stub(create_error=error)):
        assert probe().readiness is Readiness.NO_RUNTIME


def test_probe_reports_no_headset_when_the_device_is_off():
    error = _xr_exceptions().FormFactorUnavailableError()
    with _with_xr(_xr_stub(system_error=error)):
        assert probe().readiness is Readiness.NO_HEADSET


def test_probe_reports_failed_and_keeps_the_detail_of_an_unexpected_error():
    with _with_xr(_xr_stub(create_error=RuntimeError("loader exploded"))):
        result = probe()
    assert result.readiness is Readiness.FAILED
    assert "loader exploded" in result.detail


def test_probe_releases_the_instance_when_no_headset_answers():
    stub = _xr_stub(system_error=_xr_exceptions().FormFactorUnavailableError())
    with _with_xr(stub):
        probe()
    stub.destroy_instance.assert_called_once_with(stub.create_instance.return_value)


def test_launcher_for_runtime_finds_the_pimax_client_beside_the_runtime(tmp_path):
    runtime_json, client = _pimax_tree(tmp_path)
    assert launcher_for_runtime(runtime_json) == client


def test_launcher_for_runtime_returns_none_when_no_client_is_installed(tmp_path):
    runtime_json, _ = _pimax_tree(tmp_path, with_client=False)
    assert launcher_for_runtime(runtime_json) is None


def test_active_runtime_json_reads_the_openxr_registration():
    reg = MagicMock()
    reg.QueryValueEx.return_value = (r"C:\Program Files\Pimax\Runtime\PiOpenXR_64.json", 1)
    with patch("fun_time_vr.vr_runtime.winreg", reg):
        assert active_runtime_json() == Path(r"C:\Program Files\Pimax\Runtime\PiOpenXR_64.json")


def test_active_runtime_json_is_none_when_openxr_is_not_registered():
    reg = MagicMock()
    reg.OpenKey.side_effect = OSError("no such key")
    with patch("fun_time_vr.vr_runtime.winreg", reg):
        assert active_runtime_json() is None


def test_ensure_ready_starts_nothing_when_a_headset_already_answers():
    with (
        patch("fun_time_vr.vr_runtime.probe", return_value=Probe(Readiness.READY)),
        patch("fun_time_vr.vr_runtime.start_runtime") as start,
    ):
        assert ensure_ready().readiness is Readiness.READY
    start.assert_not_called()


def test_ensure_ready_starts_the_runtime_and_waits_for_the_headset(tmp_path):
    launcher = tmp_path / "PimaxClient.exe"
    with (
        patch("fun_time_vr.vr_runtime.probe",
              side_effect=[Probe(Readiness.NO_HEADSET), Probe(Readiness.READY)]),
        patch("fun_time_vr.vr_runtime.runtime_launcher", return_value=launcher),
        patch("fun_time_vr.vr_runtime.is_running", return_value=False),
        patch("fun_time_vr.vr_runtime.start_runtime") as start,
        patch("fun_time_vr.vr_runtime.time.sleep"),
    ):
        assert ensure_ready().readiness is Readiness.READY
    start.assert_called_once_with(launcher)


def test_ensure_ready_does_not_restart_a_runtime_that_is_already_up(tmp_path):
    """Already running and still no headset means the headset is off, not the
    client — restarting the client cannot power a headset on, and waiting out
    the timeout would only delay saying so."""
    launcher = tmp_path / "PimaxClient.exe"
    with (
        patch("fun_time_vr.vr_runtime.probe", return_value=Probe(Readiness.NO_HEADSET)),
        patch("fun_time_vr.vr_runtime.runtime_launcher", return_value=launcher),
        patch("fun_time_vr.vr_runtime.is_running", return_value=True),
        patch("fun_time_vr.vr_runtime.start_runtime") as start,
        patch("fun_time_vr.vr_runtime.time.sleep"),
    ):
        assert ensure_ready().readiness is Readiness.NO_HEADSET
    start.assert_not_called()


def test_ensure_ready_gives_up_when_nothing_can_be_started():
    with (
        patch("fun_time_vr.vr_runtime.probe", return_value=Probe(Readiness.NO_RUNTIME)),
        patch("fun_time_vr.vr_runtime.runtime_launcher", return_value=None),
        patch("fun_time_vr.vr_runtime.start_runtime") as start,
    ):
        assert ensure_ready().readiness is Readiness.NO_RUNTIME
    start.assert_not_called()


def test_ensure_ready_gives_up_after_the_timeout(tmp_path):
    clock = {"now": 0.0}
    fake_time = MagicMock()
    fake_time.monotonic.side_effect = lambda: clock["now"]
    fake_time.sleep.side_effect = lambda s: clock.__setitem__("now", clock["now"] + s)

    with (
        patch("fun_time_vr.vr_runtime.probe", return_value=Probe(Readiness.NO_HEADSET)),
        patch("fun_time_vr.vr_runtime.runtime_launcher",
              return_value=tmp_path / "PimaxClient.exe"),
        patch("fun_time_vr.vr_runtime.is_running", return_value=False),
        patch("fun_time_vr.vr_runtime.start_runtime"),
        patch("fun_time_vr.vr_runtime.time", fake_time),
    ):
        result = ensure_ready(timeout_s=5.0, poll_s=1.0)

    assert result.readiness is Readiness.NO_HEADSET
    assert clock["now"] >= 5.0


def test_explain_tells_the_user_to_power_on_a_missing_headset():
    message = explain(Probe(Readiness.NO_HEADSET, detail="not plugged in"))
    assert "headset" in message.lower()
    assert "FunTimeVR" in message
    assert "not plugged in" in message


def test_explain_names_the_runtime_when_there_is_none_to_talk_to():
    message = explain(Probe(Readiness.NO_RUNTIME, detail="loader found nothing"))
    assert "runtime" in message.lower()
    assert "loader found nothing" in message


def test_explain_falls_back_to_the_raw_detail_for_an_unknown_failure():
    assert "something odd" in explain(Probe(Readiness.FAILED, detail="something odd"))


def test_explain_never_raises_on_a_readiness_it_has_no_wording_for():
    """A crash inside the error path would put us back to failing silently."""
    assert explain(Probe(Readiness.READY, detail="ready"))
