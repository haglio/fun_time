from fun_time.mode_plan import build_mode_switch_plan, genau_active, nau_displays


def test_genau_active_covers_genau_and_hybrid():
    assert genau_active("genau") is True
    assert genau_active("hybrid") is True
    assert genau_active("nau") is False


def test_nau_displays_covers_nau_and_hybrid():
    # Nau owns the on-screen display in nau and hybrid; Genau owns it in genau.
    assert nau_displays("nau") is True
    assert nau_displays("hybrid") is True
    assert nau_displays("genau") is False


def test_nau_to_genau():
    plan = build_mode_switch_plan(current_mode="nau", target_mode="genau", omni_paused=False)
    assert plan.target_mode == "genau"
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd is None
    assert plan.nau_should_play is False


def test_nau_to_hybrid_keeps_nau_playing():
    # Nau already owns the display in nau; hybrid keeps Nau on-screen (Genau
    # only drives the OSR2 and paints its HUD), so Nau playback is untouched.
    plan = build_mode_switch_plan(current_mode="nau", target_mode="hybrid", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_ON"
    assert plan.nau_should_play is None


def test_genau_to_nau():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="nau", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "PAUSE"
    assert plan.hud_cmd is None
    assert plan.nau_should_play is True


def test_genau_to_hybrid_starts_nau():
    # Leaving Genau's own display for hybrid brings Nau back on-screen.  Genau
    # keeps driving as the hybrid baseline, so its RESUME is (re)asserted.
    plan = build_mode_switch_plan(current_mode="genau", target_mode="hybrid", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_ON"
    assert plan.nau_should_play is True


def test_hybrid_to_nau_keeps_nau_playing():
    plan = build_mode_switch_plan(current_mode="hybrid", target_mode="nau", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "PAUSE"
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.nau_should_play is None


def test_hybrid_to_genau_resumes_genau():
    # The per-video hybrid arbiter may have paused Genau (a funscripted video
    # was driving the OSR2).  Leaving hybrid for genau must authoritatively
    # RESUME Genau so it drives again, regardless of that transient pause.
    plan = build_mode_switch_plan(current_mode="hybrid", target_mode="genau", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.nau_should_play is False


def test_same_mode_is_noop():
    for mode in ("nau", "genau", "hybrid"):
        plan = build_mode_switch_plan(current_mode=mode, target_mode=mode, omni_paused=False)
        assert plan.is_transition is False
        assert plan.genau_cmd is None
        assert plan.hud_cmd is None
        assert plan.nau_should_play is None


def test_omnipaused_skips_transition():
    plan = build_mode_switch_plan(current_mode="nau", target_mode="genau", omni_paused=True)
    assert plan.target_mode == "genau"
    assert plan.is_transition is False
    assert plan.genau_cmd is None
    assert plan.nau_should_play is None


def test_genau_cmd_is_authoritative_for_the_target_mode():
    # Every transition asserts Genau's driving state for the target: RESUME when
    # the target drives the OSR2 with Genau (genau/hybrid), PAUSE otherwise.
    for current, target, expected in (
        ("nau", "genau", "RESUME"),
        ("nau", "hybrid", "RESUME"),
        ("genau", "hybrid", "RESUME"),
        ("hybrid", "genau", "RESUME"),
        ("genau", "nau", "PAUSE"),
        ("hybrid", "nau", "PAUSE"),
    ):
        plan = build_mode_switch_plan(current_mode=current, target_mode=target, omni_paused=False)
        assert plan.genau_cmd == expected, f"{current}->{target}"
