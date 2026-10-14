"""FunTimeVR: the Fun Time session rendered into a VR headset -- the desktop
app's own orchestrator, Funestras and controls, with one VR process compositing
every role into a single OpenXR scene.  See :mod:`fun_time_vr.orchestrator` for
the entry point and :mod:`fun_time_vr.funestras` for the VR process."""
import OpenGL

from fun_time.project_paths import apply_sibling_checkouts_to_sys_path

apply_sibling_checkouts_to_sys_path()
OpenGL.ERROR_CHECKING = False
