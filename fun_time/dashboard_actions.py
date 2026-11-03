from __future__ import annotations

QUIT_BUTTON = "quit"
OMNIMINIMIZE = "omniminimize"
OMNIRESTORE = "omnirestore"
OMNIPAUSE_TOGGLE = "omnipause_toggle"
ENTER_OMNIPAUSE = "enter_omnipause"
VOICE_TOGGLE = "voice_toggle"
HELP_REFERENCE = "help_reference"
FMODE_TOGGLE = "fmode_toggle"
RESET_ALL = "all_reset"
ENTER_VR = "enter_vr"
EXIT_VR = "exit_vr"
VR_RESET = "vr_reset"
RFB_ACTIVATE = "rfb_activate"
ORIGENERATOR_ACTIVATE = "origenerator_activate"
HEADSET_ON = "headset_on"
HEADSET_OFF = "headset_off"
HEADSET_SIGNALS = frozenset({HEADSET_ON, HEADSET_OFF})
HELP_REFERENCE_CLOSE = "help_reference_close"
HELP_REFERENCE_COMMANDS = frozenset({HELP_REFERENCE, HELP_REFERENCE_CLOSE})

REFERENCE_OPEN_FILENAME = "reference_open.txt"
LIBRARY_OPEN_FILENAME = "library_open.txt"
BROWSE_LIBRARY_CLOSE = "browse_library_close"
