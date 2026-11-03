# OpenXR's traps: graphics requirements queried before session creation, typed event casting,
# waiting for READY before the frame loop, and gating on view validity (an unlocated view reports an
# all-zero FOV).
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import glfw
import xr
from OpenGL import GL, WGL

from fun_time import preview_marker
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.win32 import draw_nothing_at_all, minimize_window, set_window_icon

from .controller_bindings import (
    ACTION_TYPES,
    AIM,
    BACK,
    CONTROLLER_BINDINGS,
    FORWARD,
    HAND_PATHS,
    STICK_CLICK,
    STICK_X,
    STICK_Y,
    TRIGGER,
)
from .gl_contexts import SharedContexts, hidden_gl_window
from .pointer import LEFT, RIGHT, HandInput
from .retiring import RetiredSwapchain, advance_retirements
from .view_tracking import views_are_renderable, views_are_tracked

if TYPE_CHECKING:
    from .frame_loop import QuadLayer

logger = logging.getLogger(__name__)


@dataclass
class SwapchainInfo:
    handle: xr.Swapchain
    width: int
    height: int
    images: list[int] = field(default_factory=list)


_LOCATED = xr.SpaceLocationFlags.ORIENTATION_VALID_BIT | xr.SpaceLocationFlags.POSITION_VALID_BIT
_NO_HANDS = {LEFT: HandInput(), RIGHT: HandInput()}


class VRSession:
    def __init__(self, *, app_name: str = "FunTimeVR") -> None:
        self.running = True
        self._window = None
        self._instance = None
        self._session = None
        self._space = None
        self._session_state = xr.SessionState.UNKNOWN
        self.views_tracked = False
        self._session_begun = False
        self.swapchains: list[SwapchainInfo] = []
        self.quad_swapchains: dict[int, SwapchainInfo] = {}
        self._retiring: list[RetiredSwapchain] = []
        self._period_logged = False
        self.view_config_views: list[xr.ViewConfigurationView] = []
        self._fbo = 0
        self._action_set = None
        self._actions: dict[str, xr.Action] = {}
        self._hand_paths: dict[str, xr.Path] = {}
        self._aim_spaces: dict[str, xr.Space] = {}
        self._actions_attached = False
        self.hands: dict[str, HandInput] = _NO_HANDS
        self._views_located = True

        self._init_glfw(app_name)
        try:
            self._init_openxr(app_name)
            self._init_actions()
            self._create_swapchains()
            self._fbo = GL.glGenFramebuffers(1)
        except Exception:
            try:
                self.close()
            except Exception:
                logger.debug("Teardown after a failed bring-up also failed", exc_info=True)
            raise


    def shared_contexts(self) -> SharedContexts:
        return SharedContexts(self._window)

    def _init_glfw(self, app_name: str) -> None:
        if not glfw.init():
            raise RuntimeError("Failed to initialize GLFW")
        try:
            self._window = hidden_gl_window(app_name)
        except RuntimeError:
            glfw.terminate()
            raise
        glfw.make_context_current(self._window)
        hwnd = glfw.get_win32_window(self._window)
        set_window_icon(hwnd, preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as()))
        preview_marker.dress_the_window(hwnd)
        draw_nothing_at_all(hwnd)
        minimize_window(hwnd, activate=False)

    def _init_openxr(self, app_name: str) -> None:
        self._instance = xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(app_name, 0, "", 0, xr.Version(1, 0, 0)),
                enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
            )
        )

        system_id = xr.get_system(
            self._instance,
            xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
        )

        self.view_config_views = xr.enumerate_view_configuration_views(
            self._instance, system_id, xr.ViewConfigurationType.PRIMARY_STEREO,
        )

        xr.get_opengl_graphics_requirements_khr(self._instance, system_id)


        graphics_binding = xr.GraphicsBindingOpenGLWin32KHR(
            h_dc=WGL.wglGetCurrentDC(),
            h_glrc=WGL.wglGetCurrentContext(),
        )

        self._session = xr.create_session(
            self._instance,
            xr.SessionCreateInfo(
                system_id=system_id,
                next=ctypes.cast(ctypes.pointer(graphics_binding), ctypes.c_void_p),
            ),
        )

        self._space = self._make_local_space()

    def _init_actions(self) -> None:
        try:
            self._action_set = xr.create_action_set(
                self._instance,
                xr.ActionSetCreateInfo(
                    action_set_name="fun_time_vr",
                    localized_action_set_name="FunTimeVR Controls",
                    priority=0,
                ),
            )
            self._hand_paths = {
                hand: xr.string_to_path(self._instance, path) for hand, path in HAND_PATHS.items()
            }
            hand_paths = list(self._hand_paths.values())
            self._actions = {
                name: xr.create_action(
                    self._action_set,
                    xr.ActionCreateInfo(
                        action_name=name,
                        action_type=action_type,
                        subaction_paths=hand_paths,
                        localized_action_name=name.replace("_", " ").title(),
                    ),
                )
                for name, action_type in ACTION_TYPES.items()
            }
            for profile_path, bindings in CONTROLLER_BINDINGS.items():
                try:
                    xr.suggest_interaction_profile_bindings(
                        self._instance,
                        xr.InteractionProfileSuggestedBinding(
                            interaction_profile=xr.string_to_path(self._instance, profile_path),
                            suggested_bindings=[
                                xr.ActionSuggestedBinding(
                                    action=self._actions[name],
                                    binding=xr.string_to_path(self._instance, input_path),
                                )
                                for name, input_paths in bindings.items()
                                for input_path in input_paths
                            ],
                        ),
                    )
                except xr.ResultException as exc:
                    logger.debug("Runtime does not take %s: %s", profile_path, exc)
            self._aim_spaces = {
                hand: xr.create_action_space(
                    self._session,
                    xr.ActionSpaceCreateInfo(action=self._actions[AIM], subaction_path=path),
                )
                for hand, path in self._hand_paths.items()
            }
            xr.attach_session_action_sets(
                self._session,
                xr.SessionActionSetsAttachInfo(action_sets=[self._action_set]),
            )
            self._actions_attached = True
            logger.info("Controllers bound: either hand points, squeezes, sizes and skips")
        except Exception:
            logger.warning(
                "No controller input: the keys and voice still work", exc_info=True
            )

    def sync_controller(self, display_time: int) -> None:
        if not self._actions_attached:
            return
        try:
            xr.sync_actions(
                self._session,
                xr.ActionsSyncInfo(
                    active_action_sets=[
                        xr.ActiveActionSet(action_set=self._action_set, subaction_path=0),
                    ],
                ),
            )
            self.hands = {
                hand: self._hand_input(hand, path, display_time)
                for hand, path in self._hand_paths.items()
            }
        except xr.ResultException:
            self.hands = _NO_HANDS

    def _float(self, name: str, path: xr.Path) -> float:
        state = xr.get_action_state_float(
            self._session,
            xr.ActionStateGetInfo(action=self._actions[name], subaction_path=path),
        )
        return state.current_state if state.is_active else 0.0

    def _pressed(self, name: str, path: xr.Path) -> bool:
        state = xr.get_action_state_boolean(
            self._session,
            xr.ActionStateGetInfo(action=self._actions[name], subaction_path=path),
        )
        return bool(state.is_active and state.current_state)

    def _hand_input(self, hand: str, path: xr.Path, display_time: int) -> HandInput:
        aim = None
        pose = xr.get_action_state_pose(
            self._session,
            xr.ActionStateGetInfo(action=self._actions[AIM], subaction_path=path),
        )
        if pose.is_active:
            location = xr.locate_space(self._aim_spaces[hand], self._space, display_time)
            if location.location_flags & _LOCATED == _LOCATED:
                position, orientation = location.pose.position, location.pose.orientation
                aim = (
                    (position.x, position.y, position.z),
                    (orientation.x, orientation.y, orientation.z, orientation.w),
                )
        return HandInput(
            aim=aim, trigger=self._float(TRIGGER, path),
            stick_x=self._float(STICK_X, path), stick_y=self._float(STICK_Y, path),
            stick_pressed=self._pressed(STICK_CLICK, path),
            forward=self._pressed(FORWARD, path), back=self._pressed(BACK, path),
        )

    def _make_local_space(self):
        return xr.create_reference_space(
            self._session,
            xr.ReferenceSpaceCreateInfo(
                reference_space_type=xr.ReferenceSpaceType.LOCAL,
                pose_in_reference_space=xr.Posef(
                    orientation=xr.Quaternionf(0, 0, 0, 1),
                    position=xr.Vector3f(0, 0, 0),
                ),
            ),
        )

    def _make_swapchain(self, width: int, height: int) -> SwapchainInfo:
        swapchain = xr.create_swapchain(
            self._session,
            xr.SwapchainCreateInfo(
                usage_flags=xr.SwapchainUsageFlags.COLOR_ATTACHMENT_BIT,
                format=GL.GL_SRGB8_ALPHA8,
                sample_count=1,
                width=width,
                height=height,
                face_count=1,
                array_size=1,
                mip_count=1,
            ),
        )
        images = xr.enumerate_swapchain_images(swapchain, xr.SwapchainImageOpenGLKHR)
        return SwapchainInfo(
            handle=swapchain, width=width, height=height,
            images=[image.image for image in images],
        )

    def _create_swapchains(self) -> None:
        for view_cfg in self.view_config_views:
            info = self._make_swapchain(
                view_cfg.recommended_image_rect_width,
                view_cfg.recommended_image_rect_height,
            )
            logger.info(
                "Swapchain %d: %dx%d, %d images",
                len(self.swapchains), info.width, info.height, len(info.images),
            )
            self.swapchains.append(info)

    def ensure_quad_swapchain(self, index: int, width: int, height: int) -> None:
        existing = self.quad_swapchains.get(index)
        if existing is not None and (existing.width, existing.height) == (width, height):
            return
        if existing is not None:
            self._retiring.append(RetiredSwapchain(existing.handle))
        self.quad_swapchains[index] = self._make_swapchain(width, height)
        logger.info("Quad swapchain %d: %dx%d", index, width, height)


    def window_close_requested(self) -> bool:
        return bool(glfw.window_should_close(self._window))

    def poll_events(self) -> None:
        while True:
            try:
                buf = xr.poll_event(self._instance)
            except xr.EventUnavailable:
                break

            if buf.type == xr.StructureType.EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING:
                old_space = self._space
                self._space = self._make_local_space()
                if old_space is not None:
                    xr.destroy_space(old_space)
                logger.info("Runtime recentered the reference space")
                continue

            if buf.type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
                event = ctypes.cast(
                    ctypes.byref(buf),
                    ctypes.POINTER(xr.EventDataSessionStateChanged),
                ).contents
                self._session_state = xr.SessionState(event.state)
                logger.info("Session state -> %s", self._session_state.name)
                if self._session_state == xr.SessionState.READY:
                    xr.begin_session(
                        self._session,
                        xr.SessionBeginInfo(
                            primary_view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                        ),
                    )
                    self._session_begun = True
                elif self._session_state == xr.SessionState.STOPPING:
                    xr.end_session(self._session)
                    self._session_begun = False
                elif self._session_state in (
                    xr.SessionState.LOSS_PENDING,
                    xr.SessionState.EXITING,
                ):
                    self.running = False

    @property
    def session_ready(self) -> bool:
        return self._session_begun

    @property
    def focused(self) -> bool:
        return self._session_state == xr.SessionState.FOCUSED

    def frame_begin(self) -> tuple[bool, int, list[xr.View]]:
        frame_state = xr.wait_frame(self._session, xr.FrameWaitInfo())
        xr.begin_frame(self._session, xr.FrameBeginInfo())
        if not self._period_logged and frame_state.predicted_display_period:
            self._period_logged = True
            period_ms = frame_state.predicted_display_period / 1e6
            logger.info(
                "Display period %.2f ms (%.1f Hz)", period_ms, 1000.0 / period_ms
            )

        should_render = bool(frame_state.should_render) and self._session_state in (
            xr.SessionState.VISIBLE, xr.SessionState.FOCUSED,
        )

        views: list[xr.View] = []
        if should_render:
            view_state, views_raw = xr.locate_views(
                self._session,
                xr.ViewLocateInfo(
                    view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                    display_time=frame_state.predicted_display_time,
                    space=self._space,
                ),
            )
            if views_are_renderable(view_state.view_state_flags):
                views = list(views_raw)
            self.views_tracked = bool(views) and views_are_tracked(
                view_state.view_state_flags)
            should_render = bool(views)
            self._note_view_locatability(bool(views))

        return should_render, frame_state.predicted_display_time, views

    def _note_view_locatability(self, located: bool) -> None:
        if located == self._views_located:
            return
        self._views_located = located
        if located:
            logger.info("Views located again; drawing to the headset once more")
        else:
            logger.warning(
                "The runtime cannot locate the views — submitting no layers, so the "
                "headset is showing its own environment until tracking returns"
            )

    def _bind_swapchain_framebuffer(self, info: SwapchainInfo) -> None:
        image_index = xr.acquire_swapchain_image(info.handle, xr.SwapchainImageAcquireInfo())
        xr.wait_swapchain_image(info.handle, xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D, info.images[image_index], 0,
        )
        GL.glViewport(0, 0, info.width, info.height)

    def bind_eye_framebuffer(self, eye_index: int) -> None:
        self._bind_swapchain_framebuffer(self.swapchains[eye_index])

    def release_eye_framebuffer(self, eye_index: int) -> None:
        GL.glFlush()
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        xr.release_swapchain_image(self.swapchains[eye_index].handle, xr.SwapchainImageReleaseInfo())

    def bind_quad_framebuffer(self, index: int) -> None:
        self._bind_swapchain_framebuffer(self.quad_swapchains[index])

    def release_quad_framebuffer(self, index: int) -> None:
        GL.glFlush()
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        xr.release_swapchain_image(
            self.quad_swapchains[index].handle, xr.SwapchainImageReleaseInfo()
        )

    def frame_end(
        self,
        display_time: int,
        views: list[xr.View],
        *,
        project: bool = True,
        quads: list[QuadLayer] | None = None,
    ) -> None:
        projection_views = [
            xr.CompositionLayerProjectionView(
                pose=view.pose,
                fov=view.fov,
                sub_image=xr.SwapchainSubImage(
                    swapchain=self.swapchains[i].handle,
                    image_rect=xr.Rect2Di(
                        offset=xr.Offset2Di(0, 0),
                        extent=xr.Extent2Di(self.swapchains[i].width, self.swapchains[i].height),
                    ),
                    image_array_index=0,
                ),
            )
            for i, view in enumerate(views)
        ]
        projection_layer = xr.CompositionLayerProjection(
            space=self._space,
            views=projection_views,
        )
        quad_structs = [
            xr.CompositionLayerQuad(
                space=self._space,
                eye_visibility=xr.EyeVisibility.BOTH,
                sub_image=xr.SwapchainSubImage(
                    swapchain=self.quad_swapchains[quad.swapchain_index].handle,
                    image_rect=xr.Rect2Di(
                        offset=xr.Offset2Di(0, 0),
                        extent=xr.Extent2Di(
                            self.quad_swapchains[quad.swapchain_index].width,
                            self.quad_swapchains[quad.swapchain_index].height,
                        ),
                    ),
                    image_array_index=0,
                ),
                pose=xr.Posef(
                    orientation=xr.Quaternionf(*quad.orientation),
                    position=xr.Vector3f(*quad.position),
                ),
                size=xr.Extent2Df(*quad.size),
            )
            for quad in (quads or [])
        ]
        layers = []
        if views and project:
            layers.append(
                ctypes.cast(
                    ctypes.pointer(projection_layer),
                    ctypes.POINTER(xr.CompositionLayerBaseHeader),
                )
            )
        if views:
            layers.extend(
                ctypes.cast(
                    ctypes.pointer(struct), ctypes.POINTER(xr.CompositionLayerBaseHeader)
                )
                for struct in quad_structs
            )
        xr.end_frame(
            self._session,
            xr.FrameEndInfo(
                display_time=display_time,
                environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                layers=layers,
            ),
        )
        self._retiring, done_waiting = advance_retirements(self._retiring)
        for handle in done_waiting:
            xr.destroy_swapchain(handle)


    def close(self) -> None:
        if self._session is not None:
            if self._session_state in (xr.SessionState.READY, xr.SessionState.SYNCHRONIZED,
                                        xr.SessionState.VISIBLE, xr.SessionState.FOCUSED):
                try:
                    xr.request_exit_session(self._session)
                except xr.ResultException:
                    pass
            for info in self.swapchains:
                xr.destroy_swapchain(info.handle)
            for info in self.quad_swapchains.values():
                xr.destroy_swapchain(info.handle)
            for retired in self._retiring:
                xr.destroy_swapchain(retired.handle)
            for space in self._aim_spaces.values():
                xr.destroy_space(space)
            if self._space is not None:
                xr.destroy_space(self._space)
            xr.destroy_session(self._session)
        if self._instance is not None:
            xr.destroy_instance(self._instance)
        if self._fbo:
            GL.glDeleteFramebuffers(1, [self._fbo])
        if self._window:
            glfw.destroy_window(self._window)
        glfw.terminate()
