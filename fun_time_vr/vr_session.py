"""OpenXR session lifecycle and swapchain management for the VR player.

Adapted from GenauVR's proven bring-up (genau_vr.vr_session), whose commit
history is a catalog of the loader's traps — graphics requirements queried
before session creation, typed event casting, waiting for READY before the
frame loop, and gating on view validity (an unlocated view reports an
all-zero FOV, which is a division by zero in the projection matrix).

Differences from GenauVR's: no controller actions (FunTimeVR is driven by the
orchestrator's hotkeys and voice), no per-eye depth buffers (the scene draws
in painter's order), and the desktop window is titled/iconed as Fun Time's.

The OpenXR/GL shell -- see CLAUDE.md, "Standing rules"; the two copies and
what waits on merging them are in docs/known-issues.md.
"""
from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass, field

import glfw
import xr
from OpenGL import GL

from fun_time.project_paths import PROJECT_ICON

logger = logging.getLogger(__name__)

_ICON_PATH = PROJECT_ICON


@dataclass
class SwapchainInfo:
    handle: xr.Swapchain
    width: int
    height: int
    images: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class QuadLayer:
    """One flat screen for the runtime's compositor to place in the world.

    The image is the most recently released one of the session's quad
    swapchain *swapchain_index*; the pose and size come from
    :func:`fun_time_vr.scene.quad_layer_placement`.
    """

    swapchain_index: int
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    size: tuple[float, float]


# A retired quad swapchain may still be referenced by the frames in flight,
# so destruction waits this many frame_end calls.
_RETIRE_AFTER_FRAMES = 3


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
        self.quad_swapchains: dict[int, SwapchainInfo] = {}
        self._retiring: list[list] = []  # [frames_left, xr.Swapchain]
        self._period_logged = False
        self.view_config_views: list[xr.ViewConfigurationView] = []
        self._fbo = 0

        self._init_glfw(app_name)
        try:
            self._init_openxr(app_name)
            self._create_swapchains()
            self._fbo = GL.glGenFramebuffers(1)
        except Exception:
            # A failed bring-up must leave nothing behind: the caller retries
            # construction when the runtime's graphics side is still warming
            # up (see the player's bring-up loop), and every attempt makes a
            # fresh window, instance and context.  close() is None-guarded
            # throughout, so it tears down exactly what got made.
            try:
                self.close()
            except Exception:
                logger.debug("Teardown after a failed bring-up also failed", exc_info=True)
            raise

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
            import ctypes.wintypes  # Windows-only, error path tolerant

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

        from OpenGL import WGL  # Windows-only binding

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
        """Have quad swapchain *index* exist at exactly *width* x *height*.

        Sized to the video texture and recreated when the clip's size changes
        (rare), so submission always uses the full image rect — no sub-rect,
        whose origin convention differs per graphics API.  The replaced chain
        is retired, not destroyed: frames in flight may still composite it.
        """
        existing = self.quad_swapchains.get(index)
        if existing is not None and (existing.width, existing.height) == (width, height):
            return
        if existing is not None:
            self._retiring.append([_RETIRE_AFTER_FRAMES, existing.handle])
        self.quad_swapchains[index] = self._make_swapchain(width, height)
        logger.info("Quad swapchain %d: %dx%d", index, width, height)

    # ------------------------------------------------------------------
    # Frame loop
    # ------------------------------------------------------------------

    def window_close_requested(self) -> bool:
        return bool(glfw.window_should_close(self._window))

    def poll_events(self) -> None:
        while True:
            try:
                buf = xr.poll_event(self._instance)
            except xr.EventUnavailable:
                break

            if buf.type == xr.StructureType.EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING:
                # The runtime re-zeroed its spaces (its own recenter, wherever
                # its UI offers one).  Recreate ours so the new origin takes
                # effect; poses already fetched this frame keep the old one,
                # which is exactly the one frame of continuity a recenter wants.
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
        """Whether the headset is worn with this app in the foreground.

        FOCUSED is the one state that proves a human is behind the lenses;
        VISIBLE also holds while the headset sits on a stand presenting to
        nobody — with its audio endpoint parked (see route_audio's caller).
        """
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
        """Submit this frame's layers: the projection layer (when *project*),
        then each :class:`QuadLayer` over it in painter's order.

        The runtime composites every layer at the true head pose each refresh,
        so a quad holds rock-steady in the world even on a frame the app took
        too long to update — flat screens as layers is exactly how a desktop
        overlay tool stays smooth over a struggling game.
        """
        # Built and kept in locals so every struct the layer pointers reference
        # stays alive until end_frame returns.
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
        for entry in self._retiring:
            entry[0] -= 1
        for entry in [entry for entry in self._retiring if entry[0] <= 0]:
            self._retiring.remove(entry)
            xr.destroy_swapchain(entry[1])

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
            for info in self.quad_swapchains.values():
                xr.destroy_swapchain(info.handle)
            for _frames_left, handle in self._retiring:
                xr.destroy_swapchain(handle)
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
