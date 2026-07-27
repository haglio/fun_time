"""GL rendering for the VR scene: video targets, screens, and immersive wraps.

Two ways a player reaches the eye.  Windowed surfaces (a flat 2D primary, the
two satellites) are curved screens from :mod:`fun_time_vr.scene`, textured
with the player's rendered frame.  Immersive projections wrap the whole view:
a full-screen pass reconstructs each pixel's world ray (GenauVR's proven
technique) and maps it into the video by projection — equirect 180 SBS,
fisheye 190 / MKX200 SBS (equidistant), or mono equirect 360.

Color is passthrough by design: mpv renders sRGB-encoded pixels into plain
RGBA8 targets, these shaders sample them undecoded, and GL_FRAMEBUFFER_SRGB
stays off — so the bytes reach the sRGB swapchain exactly as mpv wrote them.
(GenauVR sampled through an sRGB texture without re-encoding, which darkened
everything and forced its brightness=1.4 hack; this pipeline needs none.)

Every draw call rebinds the state it needs, because the mpv render contexts
share this GL context and leave bindings wherever they finished.

Not unit-tested: it needs a live GL context.  The geometry and matrices it
draws come tested from scene.py/matrices.py, and the offscreen pixel path is
MpvRenderPlayer's, verified against the real DLL.
"""
from __future__ import annotations

import ctypes

import numpy as np
from OpenGL import GL

from .projection import EQUIRECT_180_SBS, EQUIRECT_360, FISHEYE_190_SBS, MKX200_SBS

_QUAD_VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
uniform mat4 view_proj;
out vec2 frag_uv;
void main() {
    gl_Position = view_proj * vec4(pos, 1.0);
    frag_uv = uv;
}
"""

_QUAD_FRAGMENT_SHADER = """
#version 330 core
in vec2 frag_uv;
out vec4 frag_color;
uniform sampler2D video_tex;
void main() {
    frag_color = texture(video_tex, frag_uv);
}
"""

_FULLSCREEN_VERTEX_SHADER = """
#version 330 core
out vec2 screen_pos;
void main() {
    // Full-screen triangle: 3 vertices cover the viewport with no buffers.
    vec2 positions[3] = vec2[](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
    gl_Position = vec4(positions[gl_VertexID], 0.0, 1.0);
    screen_pos = positions[gl_VertexID];
}
"""

_COPY_FRAGMENT_SHADER = """
#version 330 core
in vec2 screen_pos;
out vec4 frag_color;
uniform sampler2D video_tex;
void main() {
    frag_color = texture(video_tex, screen_pos * 0.5 + 0.5);
}
"""

_IMMERSIVE_FRAGMENT_SHADER = """
#version 330 core
in vec2 screen_pos;
out vec4 frag_color;

uniform sampler2D video_tex;
uniform mat4 inv_view_proj;
uniform int eye;   // 0=left, 1=right
uniform int mode;  // see _PROJECTION_MODES

const float PI = 3.14159265359;

void main() {
    // Reconstruct this pixel's world-space ray direction.
    vec4 world_dir = inv_view_proj * vec4(screen_pos, -1.0, 1.0);
    vec3 dir = normalize(world_dir.xyz);

    // Spherical coordinates (OpenGL: +X right, +Y up, -Z forward).
    float theta = atan(dir.x, -dir.z);
    float phi = asin(clamp(dir.y, -1.0, 1.0));

    vec2 uv;
    if (mode == 4) {
        // Equirect 360, mono: the full sphere across the whole texture.
        uv = vec2(theta / (2.0 * PI) + 0.5, phi / PI + 0.5);
    } else if (mode == 1) {
        // Equirect 180, side-by-side stereo: black behind the viewer.
        if (abs(theta) > PI * 0.5) { frag_color = vec4(0.0, 0.0, 0.0, 1.0); return; }
        float u = theta / PI + 0.5;
        uv = vec2(u * 0.5 + float(eye) * 0.5, phi / PI + 0.5);
    } else {
        // Fisheye, side-by-side stereo, equidistant mapping: the ray's
        // off-axis angle sets the radius from each eye-image's center.
        float half_fov = radians(mode == 2 ? 190.0 : 200.0) * 0.5;
        float off_axis = acos(clamp(-dir.z, -1.0, 1.0));
        if (off_axis > half_fov) { frag_color = vec4(0.0, 0.0, 0.0, 1.0); return; }
        float planar_len = length(dir.xy);
        vec2 planar = planar_len > 0.0 ? dir.xy / planar_len : vec2(0.0);
        vec2 local = vec2(0.5) + (off_axis / half_fov * 0.5) * planar;
        uv = vec2(local.x * 0.5 + float(eye) * 0.5, local.y);
    }
    frag_color = texture(video_tex, uv);
}
"""

# The immersive shader's mode ids per projection; FLAT is absent because a
# flat video draws as a curved screen, not an immersive wrap.
_PROJECTION_MODES = {
    EQUIRECT_180_SBS: 1,
    FISHEYE_190_SBS: 2,
    MKX200_SBS: 3,
    EQUIRECT_360: 4,
}


def immersive_mode(projection: str) -> int | None:
    """The shader mode for *projection*, or None when it draws as a screen."""
    return _PROJECTION_MODES.get(projection)


def _compile_shader(source: str, shader_type: int) -> int:
    shader = GL.glCreateShader(shader_type)
    GL.glShaderSource(shader, source)
    GL.glCompileShader(shader)
    if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(shader).decode()
        GL.glDeleteShader(shader)
        raise RuntimeError(f"Shader compilation failed:\n{log}")
    return shader


def _compile_program(vert_src: str, frag_src: str) -> int:
    vert = _compile_shader(vert_src, GL.GL_VERTEX_SHADER)
    frag = _compile_shader(frag_src, GL.GL_FRAGMENT_SHADER)
    program = GL.glCreateProgram()
    GL.glAttachShader(program, vert)
    GL.glAttachShader(program, frag)
    GL.glLinkProgram(program)
    if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
        log = GL.glGetProgramInfoLog(program).decode()
        GL.glDeleteProgram(program)
        raise RuntimeError(f"Program link failed:\n{log}")
    GL.glDeleteShader(vert)
    GL.glDeleteShader(frag)
    return program


class RenderTarget:
    """One player's video as a texture: the FBO mpv renders into each frame.

    Plain RGBA8 (not sRGB) on purpose — see the module docstring's color note.
    Reallocated whenever the video's size changes, so the texture always holds
    the source aspect and the screens never letterbox.
    """

    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.texture = int(GL.glGenTextures(1))
        self.fbo = int(GL.glGenFramebuffers(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def ensure(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0 or (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, width, height, 0,
            GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, None,
        )
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(
            GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, self.texture, 0
        )
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"Video framebuffer incomplete: {hex(status)}")

    @property
    def ready(self) -> bool:
        return self.width > 0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9

    def close(self) -> None:
        GL.glDeleteFramebuffers(1, [self.fbo])
        GL.glDeleteTextures(1, [self.texture])


class ScreenMesh:
    """One screen's triangle strip in a static VBO.

    The strip changes only when the video's aspect does (a few times a
    session), so it is uploaded then — not rebuilt and re-uploaded on every
    draw of every eye of every frame, which is what a shared dynamic buffer
    was costing.
    """

    def __init__(self) -> None:
        self._vao = GL.glGenVertexArrays(1)
        self._vbo = GL.glGenBuffers(1)
        self.vertex_count = 0
        GL.glBindVertexArray(self._vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        stride = 5 * 4
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(1)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(3 * 4))
        GL.glBindVertexArray(0)

    def upload(self, vertices: np.ndarray) -> None:
        data = np.ascontiguousarray(vertices, dtype=np.float32)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, data.nbytes, data, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self.vertex_count = len(data)

    @property
    def ready(self) -> bool:
        return self.vertex_count > 0

    def draw(self) -> None:
        GL.glBindVertexArray(self._vao)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, self.vertex_count)
        GL.glBindVertexArray(0)

    def close(self) -> None:
        GL.glDeleteVertexArrays(1, [self._vao])
        GL.glDeleteBuffers(1, [self._vbo])


class SceneRenderer:
    """Draws one eye's view: the immersive wrap or the primary screen, then
    the satellite screens over it (painter's order keeps them on top)."""

    def __init__(self) -> None:
        self._quad_program = _compile_program(_QUAD_VERTEX_SHADER, _QUAD_FRAGMENT_SHADER)
        self._quad_view_proj = GL.glGetUniformLocation(self._quad_program, "view_proj")
        self._quad_tex = GL.glGetUniformLocation(self._quad_program, "video_tex")
        self._immersive_program = _compile_program(
            _FULLSCREEN_VERTEX_SHADER, _IMMERSIVE_FRAGMENT_SHADER
        )
        self._imm_inv_view_proj = GL.glGetUniformLocation(self._immersive_program, "inv_view_proj")
        self._imm_eye = GL.glGetUniformLocation(self._immersive_program, "eye")
        self._imm_mode = GL.glGetUniformLocation(self._immersive_program, "mode")
        self._imm_tex = GL.glGetUniformLocation(self._immersive_program, "video_tex")
        self._copy_program = _compile_program(_FULLSCREEN_VERTEX_SHADER, _COPY_FRAGMENT_SHADER)
        self._copy_tex = GL.glGetUniformLocation(self._copy_program, "video_tex")

        self._fullscreen_vao = GL.glGenVertexArrays(1)

    def begin_eye(self) -> None:
        """Reset the state the mpv render contexts may have left behind."""
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

    def draw_immersive(self, mode: int, texture: int, inv_view_proj: np.ndarray, eye: int) -> None:
        """*inv_view_proj* must already be float32 (converted once per eye by
        the caller, not per draw)."""
        GL.glUseProgram(self._immersive_program)
        GL.glUniform1i(self._imm_eye, eye)
        GL.glUniform1i(self._imm_mode, mode)
        GL.glUniform1i(self._imm_tex, 0)
        GL.glUniformMatrix4fv(self._imm_inv_view_proj, 1, GL.GL_TRUE, inv_view_proj)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glBindVertexArray(self._fullscreen_vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

    def draw_screen(self, mesh: ScreenMesh, texture: int, view_proj: np.ndarray) -> None:
        """*view_proj* must already be float32, like :meth:`draw_immersive`'s."""
        GL.glUseProgram(self._quad_program)
        GL.glUniform1i(self._quad_tex, 0)
        GL.glUniformMatrix4fv(self._quad_view_proj, 1, GL.GL_TRUE, view_proj)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        mesh.draw()
        GL.glUseProgram(0)

    def copy_texture(self, texture: int) -> None:
        """Fill the bound framebuffer's viewport with *texture*, byte-for-byte.

        How a video texture reaches a compositor-layer swapchain image: a
        plain sampling draw rather than glBlitFramebuffer, because a blit
        into an sRGB attachment leaves encode-on-write to the driver's
        discretion while this path keeps the passthrough contract (module
        docstring) that the projection path uses.
        """
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        GL.glUseProgram(self._copy_program)
        GL.glUniform1i(self._copy_tex, 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
        GL.glBindVertexArray(self._fullscreen_vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

    def close(self) -> None:
        GL.glDeleteProgram(self._quad_program)
        GL.glDeleteProgram(self._immersive_program)
        GL.glDeleteProgram(self._copy_program)
        GL.glDeleteVertexArrays(1, [self._fullscreen_vao])
