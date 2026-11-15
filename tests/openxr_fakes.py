from __future__ import annotations

import ctypes
from types import SimpleNamespace

import xr

EYE_SIZE = (640, 480)
IMAGES_PER_SWAPCHAIN = 3
WHAT_THE_INSTANCE_TAKES_WITH_IT = ("ActionSet", "Action")


def state_changed(state: xr.SessionState) -> xr.EventDataSessionStateChanged:
    event = xr.EventDataSessionStateChanged(state=state)
    event.type = xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED
    return event


def recentered() -> xr.EventDataBuffer:
    event = xr.EventDataBuffer()
    event.type = xr.StructureType.EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING
    return event


def address_of(handle) -> int:
    return ctypes.cast(handle, ctypes.c_void_p).value


class FakeOpenXR:
    def __init__(self, journal: list[str] | None = None, *, refuses_profiles=(),
                 refuses=(), should_render: bool = True) -> None:
        self.journal = [] if journal is None else journal
        self.living: dict[int, str] = {}
        self.events: list = []
        self.suggested: dict[str, list[tuple[str, str]]] = {}
        self.attached: list[int] = []
        self.ended_frames: list = []
        self._paths: dict[int, str] = {}
        self._action_names: dict[int, str] = {}
        self._refuses_profiles = set(refuses_profiles)
        self._refuses = set(refuses)
        self._should_render = should_render
        self._next_address = 0x1000

    def __getattr__(self, name: str):
        if not name[:1].isupper():
            raise AttributeError(f"the fake runtime has no {name}: no call reaches the real one")
        return getattr(xr, name)

    def _did(self, what: str) -> None:
        self.journal.append(what)
        if what in self._refuses:
            raise xr.ResultException(f"the runtime refused {what}")

    def _make(self, kind: str):
        self._did(f"create {kind}")
        address, self._next_address = self._next_address, self._next_address + 0x10
        self.living[address] = kind
        return ctypes.cast(ctypes.c_void_p(address), getattr(xr, kind))

    def _destroy(self, kind: str, handle) -> None:
        self._did(f"destroy {kind}")
        assert self.living.pop(address_of(handle)) == kind, f"destroyed a {kind} twice"

    def kinds_living(self) -> list[str]:
        return sorted(self.living.values())

    def create_instance(self, _info):
        return self._make("Instance")

    def get_system(self, _instance, _info) -> int:
        return 1

    def enumerate_view_configuration_views(self, _instance, _system, _kind):
        width, height = EYE_SIZE
        return [SimpleNamespace(recommended_image_rect_width=width,
                                recommended_image_rect_height=height)] * 2

    def get_opengl_graphics_requirements_khr(self, _instance, _system) -> None:
        self._did("ask what OpenGL it needs")

    def create_session(self, _instance, _info):
        return self._make("Session")

    def create_reference_space(self, _session, _info):
        return self._make("Space")

    def create_action_space(self, _session, _info):
        return self._make("Space")

    def destroy_space(self, space) -> None:
        self._destroy("Space", space)

    def create_action_set(self, _instance, _info):
        return self._make("ActionSet")

    def create_action(self, _action_set, info):
        action = self._make("Action")
        self._action_names[address_of(action)] = info.action_name.decode()
        return action

    def string_to_path(self, _instance, path: str) -> int:
        number = len(self._paths) + 1
        self._paths[number] = path
        return number

    def suggest_interaction_profile_bindings(self, _instance, suggested) -> None:
        profile = self._paths[suggested.interaction_profile]
        if profile in self._refuses_profiles:
            raise xr.ResultException(f"no {profile} here")
        self.suggested[profile] = sorted(
            (self._action_names[address_of(binding.action)], self._paths[binding.binding])
            for binding in suggested.suggested_bindings)

    def attach_session_action_sets(self, _session, info) -> None:
        self._did("attach the controls")
        self.attached.append(info.count_action_sets)

    def create_swapchain(self, _session, _info):
        return self._make("Swapchain")

    def enumerate_swapchain_images(self, swapchain, _kind):
        first = address_of(swapchain)
        return [SimpleNamespace(image=first + n) for n in range(IMAGES_PER_SWAPCHAIN)]

    def destroy_swapchain(self, swapchain) -> None:
        self._destroy("Swapchain", swapchain)

    def destroy_session(self, session) -> None:
        self._destroy("Session", session)

    def destroy_instance(self, instance) -> None:
        self._destroy("Instance", instance)
        self.living = {address: kind for address, kind in self.living.items()
                       if kind not in WHAT_THE_INSTANCE_TAKES_WITH_IT}

    def poll_event(self, _instance):
        if not self.events:
            raise xr.EventUnavailable
        return self.events.pop(0)

    def begin_session(self, _session, _info) -> None:
        self._did("begin the session")

    def end_session(self, _session) -> None:
        self._did("end the session")

    def request_exit_session(self, _session) -> None:
        self._did("ask the session to exit")

    def wait_frame(self, _session, _info):
        return SimpleNamespace(should_render=self._should_render,
                               predicted_display_period=11_111_111, predicted_display_time=5)

    def begin_frame(self, _session, _info) -> None:
        pass

    def locate_views(self, _session, _info):
        tracked = (xr.ViewStateFlags.ORIENTATION_VALID_BIT | xr.ViewStateFlags.POSITION_VALID_BIT
                   | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT | xr.ViewStateFlags.POSITION_TRACKED_BIT)
        fov = xr.Fovf(-0.8, 0.8, 0.7, -0.7)
        return SimpleNamespace(view_state_flags=tracked), [xr.View(fov=fov), xr.View(fov=fov)]

    def end_frame(self, _session, info) -> None:
        self.ended_frames.append(info)

    def acquire_swapchain_image(self, _swapchain, _info) -> int:
        return 1

    def wait_swapchain_image(self, _swapchain, _info) -> None:
        pass

    def release_swapchain_image(self, swapchain, _info) -> None:
        self._did(f"release an image of {address_of(swapchain):#x}")

    def sync_actions(self, _session, _info) -> None:
        pass

    def get_action_state_float(self, _session, _info):
        return SimpleNamespace(is_active=False, current_state=0)

    get_action_state_boolean = get_action_state_float

    def get_action_state_pose(self, _session, _info):
        return SimpleNamespace(is_active=False)


class FakeSceneWindow:
    def __init__(self, journal: list[str], *, asked_to_close: bool = False) -> None:
        self.journal = journal
        self.drawn_into: list[tuple[int, int, int]] = []
        self.asked_to_close = asked_to_close
        self.contexts = object()

    def open(self, title: str) -> None:
        self.journal.append(f"open the scene window {title}")

    @staticmethod
    def drawing_context() -> tuple[int, int]:
        return 0, 0

    def make_framebuffer(self) -> None:
        self.journal.append("make the framebuffer")

    def draw_into(self, texture: int, width: int, height: int) -> None:
        self.drawn_into.append((texture, width, height))

    def done_drawing(self) -> None:
        self.journal.append("done drawing")

    def close_requested(self) -> bool:
        return self.asked_to_close

    def shared_contexts(self):
        return self.contexts

    def close(self) -> None:
        self.journal.append("close the scene window")
