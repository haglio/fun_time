from __future__ import annotations

from unittest.mock import MagicMock

from fun_time.robot_hand.view import (
    apply_window_outer_bounds,
    create_robot_hand_view,
    install_tk_exception_handler,
)


class FakeRoot:
    def __init__(self):
        self.title_text = None
        self.geometry_calls: list[str] = []
        self.configure_calls: list[dict] = []
        self.iconbitmap_calls: list[str] = []
        self.report_callback_exception = None
        self.update_idletasks_calls = 0
        self.x = 10
        self.y = 20
        self.rootx = 18
        self.rooty = 52

    def title(self, value: str) -> None:
        self.title_text = value

    def geometry(self, value: str) -> None:
        self.geometry_calls.append(value)

    def configure(self, **kwargs) -> None:
        self.configure_calls.append(kwargs)

    def iconbitmap(self, value: str) -> None:
        self.iconbitmap_calls.append(value)

    def update_idletasks(self) -> None:
        self.update_idletasks_calls += 1

    def winfo_x(self) -> int:
        return self.x

    def winfo_y(self) -> int:
        return self.y

    def winfo_rootx(self) -> int:
        return self.rootx

    def winfo_rooty(self) -> int:
        return self.rooty


class FakeFrame:
    def __init__(self, parent, **kwargs):
        self.parent = parent
        self.kwargs = kwargs
        self.pack_calls: list[dict] = []

    def pack(self, **kwargs) -> None:
        self.pack_calls.append(kwargs)


class FakeLabel(FakeFrame):
    pass


class FakeStringVar:
    def __init__(self, *, value: str):
        self.value = value
        self.set_calls: list[str] = []

    def set(self, value: str) -> None:
        self.value = value
        self.set_calls.append(value)


class FakeTkModule:
    def __init__(self):
        self.root = FakeRoot()

    def Tk(self):
        return self.root

    @staticmethod
    def Frame(parent, **kwargs):
        return FakeFrame(parent, **kwargs)

    @staticmethod
    def Label(parent, **kwargs):
        return FakeLabel(parent, **kwargs)

    @staticmethod
    def StringVar(*, value: str):
        return FakeStringVar(value=value)


def test_create_robot_hand_view_builds_window_widgets():
    fake_tk = FakeTkModule()

    view = create_robot_hand_view(width=1200, height=900, x=10, y=20, tk_module=fake_tk)

    assert view.root is fake_tk.root
    assert fake_tk.root.title_text == "Robot Hand"
    assert fake_tk.root.geometry_calls == ["1200x900+10+20", "1184x860+10+20"]
    assert fake_tk.root.configure_calls == [{"bg": "black"}]
    assert view.container.kwargs == {"bg": "black"}
    assert view.container.pack_calls == [{"fill": "both", "expand": True}]
    assert view.image_label.kwargs == {"bg": "black", "bd": 0, "highlightthickness": 0}
    assert view.image_label.pack_calls == [{"fill": "both", "expand": True}]
    assert view.status_var.value == "Starting..."
    assert view.status_label.kwargs["textvariable"] is view.status_var
    assert view.status_label.kwargs["font"] == ("Consolas", 10)
    assert fake_tk.root.iconbitmap_calls == []


def test_create_robot_hand_view_sets_icon_when_present(tmp_path):
    fake_tk = FakeTkModule()
    icon_path = tmp_path / "icon.ico"
    icon_path.write_bytes(b"ico")

    create_robot_hand_view(width=1200, height=900, x=10, y=20, icon_path=icon_path, tk_module=fake_tk)

    assert fake_tk.root.iconbitmap_calls == [str(icon_path)]


def test_apply_window_outer_bounds_compensates_for_window_decorations():
    root = FakeRoot()

    apply_window_outer_bounds(root=root, width=1200, height=900, x=10, y=20)

    assert root.geometry_calls == ["1200x900+10+20", "1184x860+10+20"]
    assert root.update_idletasks_calls == 1


def test_install_tk_exception_handler_updates_status_and_shows_overlay():
    root = FakeRoot()
    logger = MagicMock()
    status_messages: list[str] = []
    overlay_shows: list[str] = []

    install_tk_exception_handler(
        root=root,
        logger=logger,
        status_setter=status_messages.append,
        show_status=lambda: overlay_shows.append("show"),
        log_name="robot_hand_listener.log",
    )

    root.report_callback_exception(RuntimeError, RuntimeError("boom"), None)

    logger.critical.assert_called_once()
    assert status_messages == ["Error: boom\nSee robot_hand_listener.log"]
    assert overlay_shows == ["show"]


def test_install_tk_exception_handler_logs_when_status_update_fails():
    root = FakeRoot()
    logger = MagicMock()

    install_tk_exception_handler(
        root=root,
        logger=logger,
        status_setter=lambda _message: (_ for _ in ()).throw(RuntimeError("status fail")),
        show_status=lambda: None,
        log_name="robot_hand_listener.log",
    )

    root.report_callback_exception(RuntimeError, RuntimeError("boom"), None)

    logger.exception.assert_called_once_with("Failed to update status after Tk exception")
