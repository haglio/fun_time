from __future__ import annotations

from dataclasses import dataclass, replace

from .projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    FISHEYE_220_SBS,
    MKX200_SBS,
    RECTILINEAR_SBS,
)

_EQUIRECT_MODE, _FISHEYE_MODE, _EQUIRECT_360_MODE, _RECTILINEAR_MODE = 1, 2, 3, 4

MIN_FOV_DEG = 40.0
_MAX_FOV_DEG = {_EQUIRECT_MODE: 180.0, _FISHEYE_MODE: 240.0, _RECTILINEAR_MODE: 170.0}
MIN_HEIGHT, MAX_HEIGHT = 0.5, 2.0

_CURVE_EQUIDISTANT, _CURVE_STEREOGRAPHIC, _CURVE_EQUISOLID = 0, 1, 2


@dataclass(frozen=True)
class Wrap:
    mode: int
    fov_deg: float = 0.0
    curve: int = _CURVE_EQUIDISTANT
    height: float = 1.0


_WRAPS = {
    EQUIRECT_180_SBS: Wrap(_EQUIRECT_MODE, 180.0),
    FISHEYE_180_SBS: Wrap(_FISHEYE_MODE, 180.0),
    FISHEYE_190_SBS: Wrap(_FISHEYE_MODE, 190.0),
    MKX200_SBS: Wrap(_FISHEYE_MODE, 200.0),
    FISHEYE_220_SBS: Wrap(_FISHEYE_MODE, 220.0),
    FISHEYE_200_STEREOGRAPHIC_SBS: Wrap(_FISHEYE_MODE, 200.0, _CURVE_STEREOGRAPHIC),
    FISHEYE_200_EQUISOLID_SBS: Wrap(_FISHEYE_MODE, 200.0, _CURVE_EQUISOLID),
    RECTILINEAR_SBS: Wrap(_RECTILINEAR_MODE, 120.0),
    EQUIRECT_360: Wrap(_EQUIRECT_360_MODE),
}

IMMERSIVE_FRAGMENT_SHADER = f"""
#version 330 core
in vec2 screen_pos;
out vec4 frag_color;

uniform sampler2D video_tex;
uniform mat4 inv_view_proj;
uniform int eye;   // 0=left, 1=right
uniform int mode;  // a Wrap's, written in from the mode ids below
uniform float fov_half;  // radians; a Wrap's, unread by the 360
uniform int curve;  // a fisheye Wrap's, unread by the rest
uniform float height;  // a Wrap's, unread by the 360

const float PI = 3.14159265359;

void main() {{
    vec4 world_dir = inv_view_proj * vec4(screen_pos, -1.0, 1.0);
    vec3 dir = normalize(world_dir.xyz);

    // OpenGL: +X right, +Y up, -Z forward.
    float theta = atan(dir.x, -dir.z);
    float phi = asin(clamp(dir.y, -1.0, 1.0));

    if (mode == {_EQUIRECT_360_MODE}) {{
        frag_color = texture(video_tex, vec2(theta / (2.0 * PI) + 0.5, phi / PI + 0.5));
        return;
    }}

    // Where in one eye's image this ray lands, 0 to 1 each way.
    vec2 local;
    if (mode == {_EQUIRECT_MODE}) {{
        local = vec2(theta, phi) / (2.0 * fov_half) + 0.5;
    }} else if (mode == {_RECTILINEAR_MODE}) {{
        if (dir.z >= 0.0) {{ frag_color = vec4(0.0, 0.0, 0.0, 1.0); return; }}
        local = dir.xy / (-dir.z * tan(fov_half)) * 0.5 + 0.5;
    }} else {{
        float off_axis = acos(clamp(-dir.z, -1.0, 1.0));
        float r;
        if (curve == {_CURVE_STEREOGRAPHIC}) {{
            r = tan(off_axis * 0.5) / tan(fov_half * 0.5);
        }} else if (curve == {_CURVE_EQUISOLID}) {{
            r = sin(off_axis * 0.5) / sin(fov_half * 0.5);
        }} else {{
            r = off_axis / fov_half;
        }}
        if (!(r >= 0.0 && r <= 1.4143)) {{ frag_color = vec4(0.0, 0.0, 0.0, 1.0); return; }}
        float planar_len = length(dir.xy);
        vec2 planar = planar_len > 0.0 ? dir.xy / planar_len : vec2(0.0);
        local = vec2(0.5) + (r * 0.5) * planar;
    }}
    local.y = 0.5 + (local.y - 0.5) / height;
    if (!(local.x >= 0.0 && local.x <= 1.0 && local.y >= 0.0 && local.y <= 1.0)) {{
        frag_color = vec4(0.0, 0.0, 0.0, 1.0);
        return;
    }}
    frag_color = texture(video_tex, vec2(local.x * 0.5 + float(eye) * 0.5, local.y));
}}
"""


def angle_limits(projection: str) -> tuple[float, float] | None:
    wrap = _WRAPS.get(projection)
    if wrap is None or wrap.mode not in _MAX_FOV_DEG:
        return None
    return MIN_FOV_DEG, _MAX_FOV_DEG[wrap.mode]


def immersive_wrap(
    projection: str, *, fov_deg: float | None = None, height: float | None = None,
) -> Wrap | None:
    wrap = _WRAPS.get(projection)
    limits = angle_limits(projection)
    if limits is None:
        return wrap
    low, high = limits
    return replace(
        wrap,
        fov_deg=wrap.fov_deg if fov_deg is None else min(max(fov_deg, low), high),
        height=wrap.height if height is None else min(max(height, MIN_HEIGHT), MAX_HEIGHT),
    )
