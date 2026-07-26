"""OpenXR session lifecycle and swapchain management for the VR player.

Adapted from GenauVR's proven bring-up (genau_vr.vr_session), whose commit
history is a catalog of the loader's traps — graphics requirements queried
before session creation, typed event casting, waiting for READY before the
frame loop, and gating on view validity (an unlocated view reports an
all-zero FOV, which is a division by zero in the projection matrix).
Consolidating the two copies into a shared sibling is part of the planned
GenauVR-engine extraction.

Differences from GenauVR's: no controller actions (FunTimeVR is driven by the
orchestrator's hotkeys and voice), no per-eye depth buffers (the scene draws
in painter's order), and the desktop window is titled/iconed as Fun Time's.

Not unit-tested: it needs the OpenXR loader, a runtime, and a live GL context.
"""
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass, field
from pathlib import Path

import glfw
import xr
from OpenGL import GL

logger = logging.getLogger(__name__)

_ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"


@dataclass
class SwapchainInfo:
    handle: xr.Swapchain
    width: int
    height: int
    images: list[int] = field(default_factory=list)


class VRSession:
    """The OpenXR instance, session, reference space, and per-eye swapchains."""

    def __init__(self, *, app_name: str = "FunTimeVR") -> None:
        self.running = True
        self._window = None
        self._instance = None
        self._session = None
        self._space = None
        self._session_state = xr.SessionState.UNKNOWN
        self._session_begun = False
        self.swapchains: list[SwapchainInfo] = []
        self.view_config_views: list[xr.ViewConfigurationView] = []
        self._fbo = 0

        self._init_glfw(app_name)
        self._init_openxr(app_name)
        self._create_swapchains()
        self._fbo = GL.glGenFramebuffers(1)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_glfw(self, app_name: str) -> None:
        """A small desktop window: it owns the GL context the whole pipeline
        (mpv render contexts included) runs on, and gives the hidden-launched
        process a taskbar presence with a close button."""
        if not glfw.init():
            raise RuntimeError("Failed to initialize GLFW")
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 5)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.DECORATED, glfw.TRUE)
        self._window = glfw.create_window(320, 200, app_name, None, None)
        if not self._window:
            glfw.terminate()
            raise RuntimeError("Failed to create GLFW window")
        glfw.make_context_current(self._window)
        self._set_window_icon()

    def _set_window_icon(self) -> None:
        """Fun Time's icon via Win32 WM_SETICON — GLFW's own icon API loses to
        the taskbar (GenauVR's commit 722df45 learned this the slow way)."""
        try:
            import ctypes.wintypes  # noqa: PLC0415 — Windows-only, error path tolerant

            hwnd = glfw.get_win32_window(self._window)
            image_icon, lr_loadfromfile, wm_seticon = 1, 0x10, 0x80
            for which, cx, cy in ((0, 16, 16), (1, 32, 32)):  # ICON_SMALL, ICON_BIG
                hicon = ctypes.windll.user32.LoadImageW(
                    None, str(_ICON_PATH), image_icon, cx, cy, lr_loadfromfile,
                )
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, wm_seticon, which, hicon)
        except Exception:
            logger.debug("Could not set window icon", exc_info=True)

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

        # The loader requires this call before create_session.
        xr.get_opengl_graphics_requirements_khr(self._instance, system_id)

        from OpenGL import WGL  # noqa: PLC0415 — Windows-only binding

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

        self._space = xr.create_reference_space(
            self._session,
            xr.ReferenceSpaceCreateInfo(
                reference_space_type=xr.ReferenceSpaceType.LOCAL,
                pose_in_reference_space=xr.Posef(
                    orientation=xr.Quaternionf(0, 0, 0, 1),
                    position=xr.Vector3f(0, 0, 0),
                ),
            ),
        )

    def _create_swapchains(self) -> None:
        for view_cfg in self.view_config_views:
            width = view_cfg.recommended_image_rect_width
            height = view_cfg.recommended_image_rect_height
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
            info = SwapchainInfo(
                handle=swapchain, width=width, height=height,
                images=[image.image for image in images],
            )
            logger.info(
                "Swapchain %d: %dx%d, %d images",
                len(self.swapchains), width, height, len(info.images),
            )
            self.swapchains.append(info)

    # ------------------------------------------------------------------
    # Frame loop
    # ------------------------------------------------------------------

    @property
    def window(self):
        return self._window

    def window_close_requested(self) -> bool:
        return bool(glfw.window_should_close(self._window))

    def poll_events(self) -> None:
        while True:
            try:
                buf = xr.poll_event(self._instance)
            except xr.EventUnavailable:
                break

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
                elif self._session_state in (
                    xr.SessionState.LOSS_PENDING,
                    xr.SessionState.EXITING,
                ):
                    self.running = False

    @property
    def session_ready(self) -> bool:
        return self._session_begun

    def frame_begin(self) -> tuple[bool, int, list[xr.View]]:
        frame_state = xr.wait_frame(self._session, xr.FrameWaitInfo())
        xr.begin_frame(self._session, xr.FrameBeginInfo())

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
            # A view's pose and FOV mean nothing until the runtime says it has
            # located them; for the first frames after the session turns
            # visible the FOV comes back all zeroes — a zero-width frustum and
            # a division by zero in the projection matrix.
            valid = (
                xr.ViewStateFlags.ORIENTATION_VALID_BIT
                | xr.ViewStateFlags.POSITION_VALID_BIT
            )
            if view_state.view_state_flags & valid == valid:
                views = list(views_raw)
            else:
                should_render = False

        return should_render, frame_state.predicted_display_time, views

    def bind_eye_framebuffer(self, eye_index: int) -> None:
        info = self.swapchains[eye_index]
        image_index = xr.acquire_swapchain_image(info.handle, xr.SwapchainImageAcquireInfo())
        xr.wait_swapchain_image(info.handle, xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
            GL.GL_TEXTURE_2D, info.images[image_index], 0,
        )
        GL.glViewport(0, 0, info.width, info.height)

    def release_eye_framebuffer(self, eye_index: int) -> None:
        GL.glFlush()
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        xr.release_swapchain_image(self.swapchains[eye_index].handle, xr.SwapchainImageReleaseInfo())

    def frame_end(self, display_time: int, views: list[xr.View]) -> None:
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
        layers = [
            ctypes.cast(ctypes.pointer(projection_layer), ctypes.POINTER(xr.CompositionLayerBaseHeader))
        ]
        xr.end_frame(
            self._session,
            xr.FrameEndInfo(
                display_time=display_time,
                environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                layers=layers if views else [],
            ),
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

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
