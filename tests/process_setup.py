import os

from app_support import haglio_settings
from hypothesis import settings

from tests.git_isolation import forget_the_enclosing_repository

QTS_WINDOWS = "QT_QPA_PLATFORM"
THE_MAIN_FUNESTRAS_PYGAME_WINDOW = "SDL_VIDEODRIVER"
NO_DISPLAY_UNLESS_A_DEVELOPER_SETS_ONE_TO_WATCH = {
    QTS_WINDOWS: "offscreen",
    THE_MAIN_FUNESTRAS_PYGAME_WINDOW: "dummy",
}

# Qt renders offscreen for the whole unit suite: each test that shows a widget would otherwise
# throw a real window on screen, and this must be set before any QApplication exists.
# tests/integration/conftest.py restores the native platform, whose real HWNDs its tests inspect.
for names_the_display, shows_nothing in NO_DISPLAY_UNLESS_A_DEVELOPER_SETS_ONE_TO_WATCH.items():
    os.environ.setdefault(names_the_display, shows_nothing)
os.environ["FUN_TIME_MUTE_AUDIO"] = "1"
os.environ[haglio_settings.SETTINGS_ENV] = haglio_settings.THE_EXAMPLE

forget_the_enclosing_repository(os.environ)

settings.register_profile("on_a_machine_other_runs_share", deadline=None)
settings.load_profile("on_a_machine_other_runs_share")
