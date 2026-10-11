from tests.integration import fix_session_notice


def test_a_click_on_the_notice_stops_the_fix_session_and_opens_it_in_claude_desktop():
    ran = []

    fix_session_notice.open_in_claude_desktop(
        "claude.exe", "1a2b3c4d", "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d", "C:/fun_time/.claude/worktrees/fix-398",
        run=lambda command, **kwargs: ran.append((command, kwargs["cwd"])))

    assert ran == [
        (["claude.exe", "stop", "1a2b3c4d"], "C:/fun_time/.claude/worktrees/fix-398"),
        (["claude.exe", "--desktop", "--resume", "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"],
         "C:/fun_time/.claude/worktrees/fix-398"),
    ]
