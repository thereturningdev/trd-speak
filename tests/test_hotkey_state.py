from flow import hotkey_state
from flow.config import Config


def test_save_then_load_round_trips_all_three_combos(tmp_path):
    p = tmp_path / "hotkeys.json"
    hotkey_state.save(["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"], path=p)
    assert hotkey_state.load(path=p) == {
        "dictate": ["ctrl", "shift"],
        "repaste": ["cmd", "ctrl"],
        "correct": ["cmd", "alt"],
    }


def test_load_missing_returns_none(tmp_path):
    assert hotkey_state.load(path=tmp_path / "absent") is None


def test_load_garbage_returns_none(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text("not json {{{")
    assert hotkey_state.load(path=p) is None


def test_resolve_prefers_valid_saved_combos(tmp_path):
    p = tmp_path / "hotkeys.json"
    # Saved combos differ from Config() defaults, to prove precedence.
    hotkey_state.save(["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"], path=p)
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"]
    )


def test_resolve_falls_back_per_combo_when_missing(tmp_path):
    p = tmp_path / "hotkeys.json"
    # A dictate value distinct from every Config() default, so the dictate
    # combo and the fallback correct combo (["cmd", "alt"]) are clearly
    # different rather than coincidentally equal.
    p.write_text('{"dictate": ["cmd", "shift"]}')
    # dictate from the file, repaste and correct from config defaults.
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "shift"], ["cmd", "ctrl"], ["cmd", "alt"]
    )


def test_resolve_falls_back_per_combo_when_invalid(tmp_path):
    p = tmp_path / "hotkeys.json"
    # dictate is invalid (empty), repaste is valid -> dictate falls back.
    p.write_text('{"dictate": [], "repaste": ["alt", "shift"]}')
    assert hotkey_state.resolve(Config(), path=p) == (
        ["ctrl", "shift"], ["alt", "shift"], ["cmd", "alt"]
    )


def test_resolve_falls_back_to_config_when_no_file(tmp_path):
    assert hotkey_state.resolve(Config(), path=tmp_path / "absent") == (
        ["ctrl", "shift"],
        ["cmd", "ctrl"],
        ["cmd", "alt"],
    )


# -- issue #26: hotkeys.json combos must also pass validate_combo -----------


def test_resolve_falls_back_when_saved_combo_is_single_bare_key(tmp_path, capsys):
    """hotkeys.json = {"repaste": ["v"]} is shape-valid (validate_keys) but a
    1-key global shortcut is unusable -- resolve must fall back to the
    config default, not arm it, and log the rejection."""
    p = tmp_path / "hotkeys.json"
    p.write_text('{"repaste": ["v"]}')
    dictate, repaste, correct = hotkey_state.resolve(Config(), path=p)
    assert repaste == ["cmd", "ctrl"]
    assert dictate == ["ctrl", "shift"]
    assert correct == ["cmd", "alt"]
    out = capsys.readouterr()
    assert "repaste" in out.out + out.err


def test_resolve_falls_back_when_saved_combo_is_bare_modifier(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text('{"dictate": ["ctrl"]}')
    dictate, _, _ = hotkey_state.resolve(Config(), path=p)
    assert dictate == ["ctrl", "shift"]


def test_resolve_falls_back_when_saved_combo_has_no_modifier(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text('{"correct": ["v", "b"]}')
    _, _, correct = hotkey_state.resolve(Config(), path=p)
    assert correct == ["cmd", "alt"]


def test_resolve_still_accepts_valid_saved_combo(tmp_path):
    """Regression: a legitimate saved combo still resolves unchanged."""
    p = tmp_path / "hotkeys.json"
    hotkey_state.save(["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"], path=p)
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "alt"], ["alt", "shift"], ["ctrl", "alt"]
    )


# -- issue #26: cross-combo duplicate check ----------------------------------


def test_dedupe_keeps_dictate_falls_back_repaste_on_identical_combo():
    cfg = Config()
    dictate, repaste, correct = hotkey_state.dedupe(
        ["ctrl", "shift"], ["ctrl", "shift"], ["cmd", "alt"]
    )
    assert dictate == ["ctrl", "shift"]
    assert repaste == cfg.repaste_keys
    assert correct == ["cmd", "alt"]


def test_dedupe_ignores_key_order_when_comparing_sets():
    cfg = Config()
    dictate, repaste, correct = hotkey_state.dedupe(
        ["ctrl", "shift"], ["shift", "ctrl"], ["cmd", "alt"]
    )
    assert repaste == cfg.repaste_keys


def test_dedupe_logs_which_combo_was_demoted(capsys):
    hotkey_state.dedupe(["ctrl", "shift"], ["ctrl", "shift"], ["cmd", "alt"])
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "repaste" in combined
    assert "dictate" in combined


def test_dedupe_priority_dictate_repaste_correct():
    """correct clashing with dictate falls back to correct's own default,
    even though repaste (which comes between them) is distinct."""
    cfg = Config()
    dictate, repaste, correct = hotkey_state.dedupe(
        ["ctrl", "shift"], ["cmd", "ctrl"], ["ctrl", "shift"]
    )
    assert dictate == ["ctrl", "shift"]
    assert repaste == ["cmd", "ctrl"]
    assert correct == cfg.correct_keys


def test_dedupe_all_three_identical_falls_back_second_and_third():
    cfg = Config()
    dictate, repaste, correct = hotkey_state.dedupe(
        ["cmd", "ctrl"], ["cmd", "ctrl"], ["cmd", "ctrl"]
    )
    assert dictate == ["cmd", "ctrl"]
    assert repaste == cfg.repaste_keys
    assert correct == cfg.correct_keys


def test_dedupe_no_clash_returns_combos_unchanged():
    result = hotkey_state.dedupe(
        ["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"]
    )
    assert result == (["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])


def test_dedupe_falls_back_to_built_in_default_not_caller_supplied_value():
    """Regression for a real bug: dedupe must fall back a demoted combo to
    the app's hardcoded built-in default, never to "whatever the caller's
    config object currently holds for that role" -- if config.toml itself
    set both [hotkey] and [repaste] to the same combo (the issue's own
    example), the caller's config.repaste_keys IS the duplicate, so using it
    as the fallback would be a no-op and leave the duplicate armed."""
    dictate, repaste, correct = hotkey_state.dedupe(
        ["ctrl", "shift"], ["ctrl", "shift"], ["cmd", "alt"]
    )
    assert repaste == Config().repaste_keys
    assert repaste != dictate


def test_dedupe_full_pipeline_config_toml_duplicate_falls_back(tmp_path):
    """End-to-end (issue #26's own motivating example): a hand-edited
    config.toml sets [hotkey] and [repaste] to the identical combo. Running
    the real load_config -> resolve -> dedupe pipeline (as flow.menubar.run
    does) must land on two DIFFERENT combos, not silently keep the clash."""
    from flow.config import load_config

    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", "shift"]\n\n[repaste]\nkeys = ["ctrl", "shift"]\n')
    cfg = load_config(str(p))
    assert cfg.keys == cfg.repaste_keys == ["ctrl", "shift"]  # the clash, as configured

    resolved = hotkey_state.resolve(cfg, path=tmp_path / "no-such-hotkeys.json")
    dictate, repaste, correct = hotkey_state.dedupe(*resolved)
    assert dictate == ["ctrl", "shift"]
    assert repaste != dictate
    assert repaste == Config().repaste_keys


def test_dedupe_logs_residual_conflict_when_own_default_also_clashes(capsys):
    """Pathological corner case: dictate is explicitly configured to
    repaste's own built-in default (cmd+ctrl), and repaste duplicates
    dictate. Falling repaste back to ITS OWN default cannot escape the
    clash -- dedupe must not crash or silently pretend it resolved things;
    it logs the residual collision loudly."""
    dictate, repaste, correct = hotkey_state.dedupe(
        ["cmd", "ctrl"], ["cmd", "ctrl"], ["cmd", "alt"]
    )
    assert dictate == ["cmd", "ctrl"]
    assert repaste == ["cmd", "ctrl"]  # own default, still equal to dictate
    out = capsys.readouterr()
    assert "ALSO duplicates" in out.out + out.err
