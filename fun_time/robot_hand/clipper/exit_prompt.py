from __future__ import annotations

from .state import restore_original_session

EXIT_PROMPT_CHOICES = ("save", "discard", "cancel")
EXIT_PROMPT_BUTTON_NAMES = {
    "save": "exit_save",
    "discard": "exit_discard",
    "cancel": "exit_cancel",
}


def cycle_exit_prompt_focus(state) -> None:
    current = state.exit_prompt_focus if state.exit_prompt_focus in EXIT_PROMPT_CHOICES else "save"
    next_index = (EXIT_PROMPT_CHOICES.index(current) + 1) % len(EXIT_PROMPT_CHOICES)
    state.exit_prompt_focus = EXIT_PROMPT_CHOICES[next_index]
    state.render_rev += 1


def queue_exit_prompt_action(state, choice: str | None = None) -> None:
    selected = choice if choice in EXIT_PROMPT_CHOICES else state.exit_prompt_focus
    if selected not in EXIT_PROMPT_CHOICES:
        selected = "save"
    state.exit_prompt_focus = selected
    state.exit_prompt_action = selected
    state.render_rev += 1


def show_exit_prompt(state) -> None:
    if state.exit_prompt_focus not in EXIT_PROMPT_CHOICES:
        state.exit_prompt_focus = "save"
    state.exit_prompt_visible = True
    state.exit_prompt_action = ""
    state.render_rev += 1


def finish_exit_prompt_action(state, choice: str) -> bool:
    if choice == "cancel":
        state.exit_prompt_visible = False
        state.exit_prompt_focus = "save"
        state.exit_prompt_action = ""
        state.render_rev += 1
        return False
    if choice == "discard":
        restore_original_session(state)
    else:
        state.autosave_session()
    state.exit_prompt_visible = False
    state.exit_prompt_focus = "save"
    state.exit_prompt_action = ""
    return True


def request_exit(state) -> bool:
    if not state.dirty:
        return True
    if not state.should_prompt_on_exit:
        state.autosave_session()
        return True
    show_exit_prompt(state)
    return False
