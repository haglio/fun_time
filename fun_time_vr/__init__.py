"""FunTimeVR: the Fun Time session rendered into a VR headset -- the desktop
app's own orchestrator, Funestras and controls, with one VR process compositing
every role into a single OpenXR scene.  See :mod:`fun_time_vr.orchestrator` for
the entry point and :mod:`fun_time_vr.funestras` for the VR process."""
import OpenGL

OpenGL.ERROR_CHECKING = False
