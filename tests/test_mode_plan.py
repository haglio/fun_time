from fun_time.mode_plan import build_mode_switch_plan


def test_vlc_to_genau():
    plan = build_mode_switch_plan(current_mode="vlc", target_mode="genau", omni_paused=False)
    assert plan.target_mode == "genau"
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd is None
    assert plan.vlc_should_play is False


def test_vlc_to_hybrid():
    plan = build_mode_switch_plan(current_mode="vlc", target_mode="hybrid", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "RESUME"
    assert plan.hud_cmd == "HUD_ON"
    assert plan.vlc_should_play is None


def test_genau_to_vlc():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="vlc", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "PAUSE"
    assert plan.hud_cmd is None
    assert plan.vlc_should_play is True


def test_genau_to_hybrid():
    plan = build_mode_switch_plan(current_mode="genau", target_mode="hybrid", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd is None
    assert plan.hud_cmd == "HUD_ON"
    assert plan.vlc_should_play is True


def test_hybrid_to_vlc():
    plan = build_mode_switch_plan(current_mode="hybrid", target_mode="vlc", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd == "PAUSE"
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.vlc_should_play is None


def test_hybrid_to_genau():
    plan = build_mode_switch_plan(current_mode="hybrid", target_mode="genau", omni_paused=False)
    assert plan.is_transition is True
    assert plan.genau_cmd is None
    assert plan.hud_cmd == "HUD_OFF"
    assert plan.vlc_should_play is False


def test_same_mode_is_noop():
    for mode in ("vlc", "genau", "hybrid"):
        plan = build_mode_switch_plan(current_mode=mode, target_mode=mode, omni_paused=False)
        assert plan.is_transition is False
        assert plan.genau_cmd is None
        assert plan.hud_cmd is None
        assert plan.vlc_should_play is None


def test_omnipaused_skips_transition():
    plan = build_mode_switch_plan(current_mode="vlc", target_mode="genau", omni_paused=True)
    assert plan.target_mode == "genau"
    assert plan.is_transition is False
    assert plan.genau_cmd is None
    assert plan.vlc_should_play is None
