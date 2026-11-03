import os

from app_support import haglio_settings
from hypothesis import settings

from tests.git_isolation import forget_the_enclosing_repository

# Qt renders offscreen for the whole unit suite: each test that shows a widget would otherwise
# throw a real window on screen, and this must be set before any QApplication exists.
# tests/integration/conftest.py restores the native platform, whose real HWNDs its tests inspect.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ["FUN_TIME_MUTE_AUDIO"] = "1"
os.environ[haglio_settings.SETTINGS_ENV] = haglio_settings.THE_EXAMPLE

forget_the_enclosing_repository(os.environ)

settings.register_profile("on_a_machine_other_runs_share", deadline=None)
settings.load_profile("on_a_machine_other_runs_share")
