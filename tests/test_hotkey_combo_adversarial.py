"""Adversarial tests for issue #26's hotkey-combo validation/dedupe contract.

Targets:
  - flow.hotkey_state.resolve
  - flow.hotkey_state.dedupe
  - flow.config.load_config's hotkey-combo validation path
    (flow.config._validate_combo_or_default)

Contract under test (from the issue and the targets' own docstrings):
  * A combo must be 2-3 keys with >=1 modifier (cmd/ctrl/alt/shift) to be a
    usable global hotkey (flow.hotkey.validate_combo).
  * config.toml/hotkeys.json values that are well-formed in SHAPE
    (validate_keys: 1-3 non-empty strings) but fail the usability rule must
    NEVER raise -- they fall back per-combo to the built-in Config()
    default and log loudly (a print(...) naming the setting).
  * Shape violations in config.toml (wrong type, wrong count, non-string
    items) still raise ValueError (pre-existing, unchanged contract).
  * hotkeys.json shape violations never raise -- resolve() always falls
    back silently (or loudly, for the usability-only case).
  * dedupe(dictate, repaste, correct) is a cross-combo, order-independent,
    priority dictate > repaste > correct check on the three FINAL resolved
    combos: any later duplicate falls back to ITS OWN Config() default
    (never to whatever the caller's config currently holds) and logs
    loudly, including a residual-collision log when even the own default
    still clashes.

Every test documents, in its body/docstring, what SHOULD happen per that
contract. Failing tests are kept as failing -- they document real bugs
found by this adversarial pass; do not weaken their assertions to make
them pass.
"""

import json

import pytest

from flow import hotkey_state
from flow.config import Config, load_config


# ============================================================================
# Section 1: all-three-identical duplication
# ============================================================================


def test_A1_dedupe_direct_all_three_identical():
    """dedupe() called directly: all three combos identical -> dictate wins,
    repaste and correct fall back to their OWN Config() defaults."""
    cfg = Config()
    d, r, c = hotkey_state.dedupe(["ctrl", "shift"], ["ctrl", "shift"], ["ctrl", "shift"])
    assert d == ["ctrl", "shift"]
    assert r == cfg.repaste_keys
    assert c == cfg.correct_keys


def test_A2_all_three_identical_via_hotkeys_json(tmp_path):
    """Full pipeline: hotkeys.json sets all three combos to the same value.
    resolve() must accept all three (each is individually a valid 2-key
    modifier combo), then dedupe() must demote repaste and correct."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({
        "dictate": ["ctrl", "alt"],
        "repaste": ["ctrl", "alt"],
        "correct": ["ctrl", "alt"],
    }))
    resolved = hotkey_state.resolve(Config(), path=p)
    assert resolved == (["ctrl", "alt"], ["ctrl", "alt"], ["ctrl", "alt"])
    d, r, c = hotkey_state.dedupe(*resolved)
    cfg = Config()
    assert d == ["ctrl", "alt"]
    assert r == cfg.repaste_keys
    assert c == cfg.correct_keys


def test_A3_all_three_identical_via_config_toml(tmp_path):
    """Full pipeline: config.toml sets [hotkey]/[repaste]/[correct] to the
    same combo (issue #26's own motivating scenario, extended to all 3)."""
    p = tmp_path / "config.toml"
    p.write_text(
        '[hotkey]\nkeys = ["ctrl", "shift"]\n'
        '\n[repaste]\nkeys = ["ctrl", "shift"]\n'
        '\n[correct]\nkeys = ["ctrl", "shift"]\n'
    )
    cfg = load_config(str(p))
    assert cfg.keys == cfg.repaste_keys == cfg.correct_keys == ["ctrl", "shift"]
    resolved = hotkey_state.resolve(cfg, path=tmp_path / "no-hotkeys.json")
    d, r, c = hotkey_state.dedupe(*resolved)
    defaults = Config()
    assert d == ["ctrl", "shift"]
    assert r == defaults.repaste_keys
    assert c == defaults.correct_keys
    assert len({tuple(sorted(x)) for x in (d, r, c)}) == 3, "all three must end up distinct"


# ============================================================================
# Section 2: pairwise duplication, all three pairings
# ============================================================================


def test_B1_dictate_repaste_duplicate_direct():
    d, r, c = hotkey_state.dedupe(["cmd", "alt"], ["cmd", "alt"], ["ctrl", "shift"])
    assert d == ["cmd", "alt"]
    assert r == Config().repaste_keys
    assert c == ["ctrl", "shift"]


def test_B2_dictate_correct_duplicate_direct():
    """correct clashes with dictate; repaste (in between, distinct) is untouched."""
    d, r, c = hotkey_state.dedupe(["cmd", "alt"], ["cmd", "ctrl"], ["cmd", "alt"])
    assert d == ["cmd", "alt"]
    assert r == ["cmd", "ctrl"]
    assert c == Config().correct_keys


def test_B3_repaste_correct_duplicate_direct():
    d, r, c = hotkey_state.dedupe(["ctrl", "shift"], ["cmd", "alt"], ["cmd", "alt"])
    assert d == ["ctrl", "shift"]
    assert r == ["cmd", "alt"]
    assert c == Config().correct_keys


def test_B4_dictate_repaste_duplicate_via_hotkeys_json(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({
        "dictate": ["alt", "shift"],
        "repaste": ["alt", "shift"],
    }))
    resolved = hotkey_state.resolve(Config(), path=p)
    assert resolved == (["alt", "shift"], ["alt", "shift"], ["cmd", "alt"])
    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == ["alt", "shift"]
    assert r == Config().repaste_keys
    assert c == ["cmd", "alt"]


def test_B5_dictate_correct_duplicate_via_hotkeys_json(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({
        "dictate": ["cmd", "shift"],
        "correct": ["cmd", "shift"],
    }))
    resolved = hotkey_state.resolve(Config(), path=p)
    assert resolved == (["cmd", "shift"], ["cmd", "ctrl"], ["cmd", "shift"])
    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == ["cmd", "shift"]
    assert r == ["cmd", "ctrl"]
    assert c == Config().correct_keys


def test_B6_repaste_correct_duplicate_via_hotkeys_json(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({
        "repaste": ["ctrl", "alt"],
        "correct": ["ctrl", "alt"],
    }))
    resolved = hotkey_state.resolve(Config(), path=p)
    assert resolved == (["ctrl", "shift"], ["ctrl", "alt"], ["ctrl", "alt"])
    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == ["ctrl", "shift"]
    assert r == ["ctrl", "alt"]
    assert c == Config().correct_keys


def test_B7_dictate_repaste_duplicate_via_config_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["cmd", "shift"]\n\n[repaste]\nkeys = ["cmd", "shift"]\n')
    cfg = load_config(str(p))
    resolved = hotkey_state.resolve(cfg, path=tmp_path / "absent.json")
    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == ["cmd", "shift"]
    assert r == Config().repaste_keys
    assert c == cfg.correct_keys


def test_B8_repaste_correct_duplicate_via_config_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[repaste]\nkeys = ["ctrl", "alt"]\n\n[correct]\nkeys = ["ctrl", "alt"]\n')
    cfg = load_config(str(p))
    resolved = hotkey_state.resolve(cfg, path=tmp_path / "absent.json")
    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == cfg.keys
    assert r == ["ctrl", "alt"]
    assert c == Config().correct_keys


# ============================================================================
# Section 3: too-short-AND-would-collide ordering
# ============================================================================


def test_C1_too_short_combo_falls_back_before_dedupe_not_colliding_as_raw(tmp_path, capsys):
    """dictate is validly configured to cmd+ctrl. repaste in hotkeys.json is
    ["ctrl"] -- shape-valid (1 non-empty string) but too short to be a usable
    combo. Per the contract, resolve() must reject the too-short repaste
    BEFORE dedupe ever sees it, falling back to repaste's config/own
    default (cmd+ctrl) -- which THEN collides with dictate, and dedupe must
    catch that residual collision (it cannot fully resolve it, since
    repaste's own default IS the clash, but it must log loudly and not
    crash)."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"dictate": ["cmd", "ctrl"], "repaste": ["ctrl"]}))
    resolved = hotkey_state.resolve(Config(), path=p)
    # resolve() must not have kept the too-short raw ["ctrl"] anywhere.
    assert ["ctrl"] not in resolved
    assert resolved == (["cmd", "ctrl"], ["cmd", "ctrl"], ["cmd", "alt"])
    out = capsys.readouterr()
    assert "repaste" in out.out + out.err  # resolve's own loud rejection log

    d, r, c = hotkey_state.dedupe(*resolved)
    assert d == ["cmd", "ctrl"]
    # repaste could not be rescued (its own default IS the clash) but must
    # never crash and must still log the residual collision loudly.
    assert r == Config().repaste_keys
    out2 = capsys.readouterr()
    assert "ALSO duplicates" in out2.out + out2.err


def test_C2_too_short_combo_own_default_does_not_collide_resolves_cleanly(tmp_path):
    """Same ordering check, but this time the too-short combo's fallback
    default does NOT collide with anything -- proving the common (non
    residual) case works too: dictate stays default, repaste's too-short
    ["v"] falls back cleanly to cmd+ctrl (no collision with dictate's
    ctrl+shift)."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"repaste": ["v"]}))
    resolved = hotkey_state.resolve(Config(), path=p)
    assert resolved == (["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])
    d, r, c = hotkey_state.dedupe(*resolved)
    assert (d, r, c) == (["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])


# ============================================================================
# Section 4: hotkeys.json shape/type edge cases
# ============================================================================


def test_D1_empty_hotkeys_json_object_falls_back_to_all_defaults(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text("{}")
    assert hotkey_state.resolve(Config(), path=p) == (
        ["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"]
    )


def test_D2_hotkeys_json_only_repaste_present(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"repaste": ["alt", "shift"]}))
    assert hotkey_state.resolve(Config(), path=p) == (
        ["ctrl", "shift"], ["alt", "shift"], ["cmd", "alt"]
    )


def test_D3_hotkeys_json_only_correct_present(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"correct": ["ctrl", "alt"]}))
    assert hotkey_state.resolve(Config(), path=p) == (
        ["ctrl", "shift"], ["cmd", "ctrl"], ["ctrl", "alt"]
    )


def test_D4_hotkeys_json_only_dictate_present(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"dictate": ["cmd", "shift"]}))
    assert hotkey_state.resolve(Config(), path=p) == (
        ["cmd", "shift"], ["cmd", "ctrl"], ["cmd", "alt"]
    )


@pytest.mark.parametrize(
    "role,bad_value",
    [
        ("dictate", None),
        ("dictate", {"a": 1}),
        ("dictate", True),
        ("dictate", False),
        ("dictate", 5),
        ("dictate", "ctrl"),
        ("dictate", [1, 2]),
        ("repaste", None),
        ("repaste", {"a": 1}),
        ("repaste", True),
        ("repaste", 5),
        ("repaste", "ctrl"),
        ("repaste", [1, 2]),
        ("correct", None),
        ("correct", {"a": 1}),
        ("correct", True),
        ("correct", 5),
        ("correct", "ctrl"),
        ("correct", [1, 2]),
    ],
)
def test_D5_hotkeys_json_non_list_or_non_string_items_never_raises(tmp_path, role, bad_value):
    """A hotkeys.json value that is not a list of strings at all (null,
    dict, bool, int, bare string, list of non-strings) must never raise --
    resolve() must silently fall back to the config default for that role."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({role: bad_value}))
    cfg = Config()
    defaults = {"dictate": cfg.keys, "repaste": cfg.repaste_keys, "correct": cfg.correct_keys}
    resolved = hotkey_state.resolve(cfg, path=p)
    resolved_by_role = dict(zip(("dictate", "repaste", "correct"), resolved))
    assert resolved_by_role[role] == defaults[role]


def test_D6_hotkeys_json_empty_list_falls_back(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"dictate": []}))
    assert hotkey_state.resolve(Config(), path=p)[0] == ["ctrl", "shift"]


# ============================================================================
# Section 5: config.toml empty [hotkey]-style table present, no "keys"
# ============================================================================


def test_E1_config_toml_empty_hotkey_table_no_keys_key(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[hotkey]\n")
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"]


def test_E2_config_toml_empty_repaste_table_no_keys_key(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[repaste]\n")
    cfg = load_config(str(p))
    assert cfg.repaste_keys == ["cmd", "ctrl"]


def test_E3_config_toml_empty_correct_table_no_keys_key(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[correct]\n")
    cfg = load_config(str(p))
    assert cfg.correct_keys == ["cmd", "alt"]


def test_E4_config_toml_all_three_tables_present_but_empty(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[hotkey]\n\n[repaste]\n\n[correct]\n")
    cfg = load_config(str(p))
    assert (cfg.keys, cfg.repaste_keys, cfg.correct_keys) == (
        ["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"]
    )


# ============================================================================
# Section 6: config.toml keys value that is not a list at all -- must raise
# ============================================================================


@pytest.mark.parametrize(
    "toml_value",
    [
        '"ctrl"',           # bare string
        "5",                # number
        "5.5",              # float
        "true",             # bool
        "{a = 1}",           # inline table / dict
        '["ctrl", 2]',       # list with a non-string item
        '[1, 2]',            # list of non-string items
    ],
)
def test_F1_config_toml_keys_non_list_shape_raises(tmp_path, toml_value):
    """Pre-existing (unchanged) contract: a config.toml keys value that is
    not a well-formed list of 1-3 non-empty strings must raise ValueError,
    never silently fall back and never crash with something other than
    ValueError."""
    p = tmp_path / "config.toml"
    p.write_text(f"[hotkey]\nkeys = {toml_value}\n")
    with pytest.raises(ValueError):
        load_config(str(p))


def test_F2_config_toml_keys_null_is_not_expressible_in_toml(tmp_path):
    """TOML has no null/None literal (unlike JSON) -- writing `keys =` with
    nothing, or `keys = nil`, is a TOML syntax error, not a value for
    load_config to validate. This documents that the null case is only
    reachable via hotkeys.json (see test_D5), not config.toml."""
    p = tmp_path / "config.toml"
    p.write_text("[hotkey]\nkeys = nil\n")
    with pytest.raises(Exception):
        # tomllib itself rejects this at parse time (TOMLDecodeError, a
        # ValueError subclass) -- never gets to our validation code.
        load_config(str(p))


# ============================================================================
# Section 7: mixed-case tokens
# ============================================================================


def test_G1_mixed_case_config_toml_resolves_lowercase(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["CTRL", "Shift"]\n')
    assert load_config(str(p)).keys == ["ctrl", "shift"]


def test_G2_mixed_case_repaste_config_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[repaste]\nkeys = ["Cmd", "ALT"]\n')
    assert load_config(str(p)).repaste_keys == ["cmd", "alt"]


def test_G3_mixed_case_hotkeys_json_resolves_lowercase(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"correct": ["Cmd", "ALT", "r"]}))
    _, _, correct = hotkey_state.resolve(Config(), path=p)
    assert correct == ["cmd", "alt", "r"]


def test_G4_mixed_case_dedupe_direct_still_matches_as_duplicate():
    """dedupe() receives already-lowercased combos from resolve() in the
    real pipeline; called directly with mixed case it must NOT
    case-normalize (that's resolve's job) -- two differently-cased spellings
    of the same combo passed directly to dedupe are DIFFERENT strings and so
    are NOT treated as duplicates. This documents dedupe's actual contract:
    it compares the strings it's given, verbatim."""
    d, r, c = hotkey_state.dedupe(["ctrl", "shift"], ["CTRL", "SHIFT"], ["cmd", "alt"])
    # Since dedupe does no case folding, "CTRL"/"SHIFT" != "ctrl"/"shift" as
    # a frozenset comparison, so repaste is NOT considered a duplicate here.
    assert r == ["CTRL", "SHIFT"]


# ============================================================================
# Section 8: whitespace-padded tokens
# ============================================================================


def test_H1_whitespace_padded_both_tokens_hotkeys_json_normalized_and_accepted(tmp_path):
    """[" ctrl", "shift "] -- validate_keys does not strip whitespace, but
    flow.hotkey.canonicalize_combo (used by resolve() once validate_combo
    passes) does: both tokens ARE real keys once trimmed, so this is a
    legitimate combo and must be normalized to ["ctrl", "shift"], not
    rejected outright (rejecting a combo that is actually usable once
    trimmed would be over-strict and inconsistent with how the real
    HotkeyListener -- which also strips via _parse_key_name -- would treat
    the same saved value)."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"repaste": [" ctrl", "shift "]}))
    dictate, repaste, correct = hotkey_state.resolve(Config(), path=p)
    assert repaste == ["ctrl", "shift"]


def test_H2_whitespace_only_token_hotkeys_json_rejected_falls_back(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"dictate": ["ctrl", " "]}))
    dictate, _, _ = hotkey_state.resolve(Config(), path=p)
    assert dictate == ["ctrl", "shift"], (
        f"BUG: a whitespace-only token alongside one real modifier "
        f"('ctrl', ' ') must not be accepted as a usable 2-key combo -- "
        f"got {dictate!r}. validate_combo only checks that at least one "
        f"token IS a modifier name; it never validates that every OTHER "
        f"token is a real, usable key. The saved combo ['ctrl', ' '] "
        f"passes validate_combo (len==2, has a modifier) and is returned "
        f"as-is by resolve(), even though ' ' is not a constructible "
        f"hotkey token (flow.hotkey._parse_key_name(' ') raises "
        f"ValueError: 'Unknown hotkey name'). This combo would crash when "
        f"actually used to build a real HotkeyListener."
    )


def test_H3_whitespace_padded_config_toml_normalized_and_accepted(tmp_path):
    """Same normalization as test_H1, through the config.toml entry point:
    [" ctrl", "shift "] are both real keys once trimmed, so load_config
    must store the canonical, trimmed form -- not the raw padded strings
    (which would display/persist with stray whitespace) and not reject a
    combo that is actually perfectly usable."""
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = [" ctrl", "shift "]\n')
    cfg = load_config(str(p))
    assert cfg.correct_keys == ["ctrl", "shift"]


def test_H4_whitespace_only_second_token_config_toml_loophole(tmp_path):
    """Same loophole as test_H2, exercised through the config.toml/
    load_config entry point instead of hotkeys.json."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", " "]\n')
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"], (
        f"BUG: load_config accepted ['ctrl', ' '] as a usable hotkey.keys "
        f"combo -- got {cfg.keys!r}. Same root cause as test_H2: "
        f"validate_combo does not validate that every token is a real key."
    )


# ============================================================================
# Section 9: duplicate tokens within a single combo
# ============================================================================


def test_I1_duplicate_token_combo_config_toml_loophole(tmp_path):
    """['ctrl', 'ctrl'] is shape-valid (2 non-empty strings) and
    validate_combo counts len(keys) == 2 with a modifier present, so it is
    ACCEPTED as a "2-key" combo -- but it is really only ONE distinct
    physical key (ctrl) duplicated. This is exactly the class of unusable
    "fires on every press of one key" combo the >=2-keys rule exists to
    reject (see the ['v'] and ['ctrl'] tests in test_config_hotkey.py).
    Per the spirit of the issue's "2-3 keys" rule this should fall back to
    the default; the actual implementation does not deduplicate tokens
    before counting, so this documents a real gap."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", "ctrl"]\n')
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"], (
        f"BUG: load_config accepted ['ctrl', 'ctrl'] as a valid 2-key "
        f"hotkey combo (got {cfg.keys!r}); it is actually one physical key "
        f"duplicated and should be rejected/fall back like ['ctrl'] does."
    )


def test_I2_duplicate_token_combo_hotkeys_json_loophole(tmp_path):
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"repaste": ["ctrl", "ctrl"]}))
    _, repaste, _ = hotkey_state.resolve(Config(), path=p)
    assert repaste == ["cmd", "ctrl"], (
        f"BUG: resolve() accepted ['ctrl', 'ctrl'] as a valid repaste "
        f"combo (got {repaste!r}); same duplicate-token loophole as "
        f"test_I1, reached through hotkeys.json instead of config.toml."
    )


def test_I3_three_duplicate_tokens_all_same_key(tmp_path):
    """['ctrl', 'ctrl', 'ctrl'] -- 3 shape-valid tokens, all the same
    physical key. Still just one real key."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", "ctrl", "ctrl"]\n')
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"], (
        f"BUG: load_config accepted ['ctrl', 'ctrl', 'ctrl'] (got "
        f"{cfg.keys!r}) -- three copies of one physical key, not a usable "
        f"3-key combo."
    )


def test_I4_duplicate_token_plus_distinct_modifier_still_rejected(tmp_path, capsys):
    """['ctrl', 'ctrl', 'shift'] claims to be 3 keys but is really 2
    distinct physical keys (ctrl, shift) plus one repeat. validate_combo
    rejects ANY repeated key outright (rather than silently collapsing the
    list to its distinct-key set and accepting that) -- reinterpreting the
    user's list behind their back is more surprising than a clear rejection
    + loud log telling them exactly why, and is consistent with test_I1's
    ['ctrl', 'ctrl'] and test_I3's ['ctrl', 'ctrl', 'ctrl'] both being
    rejected rather than silently reduced."""
    p = tmp_path / "config.toml"
    p.write_text('[hotkey]\nkeys = ["ctrl", "ctrl", "shift"]\n')
    cfg = load_config(str(p))
    assert cfg.keys == ["ctrl", "shift"]  # falls back to the default
    out = capsys.readouterr()
    assert "same key twice" in out.out + out.err


def test_I5_dedupe_direct_does_not_crash_on_internally_duplicated_combo():
    """dedupe() itself must never crash even if handed a combo with internal
    duplicate tokens (it only does cross-combo set comparison, not
    per-combo shape validation -- that's resolve()'s job upstream)."""
    d, r, c = hotkey_state.dedupe(["ctrl", "ctrl"], ["cmd", "alt"], ["cmd", "ctrl"])
    assert d == ["ctrl", "ctrl"]  # passed through unchanged, no crash
    assert r == ["cmd", "alt"]
    assert c == ["cmd", "ctrl"]


def test_I6_dedupe_treats_internally_duplicated_combo_as_its_reduced_set():
    """['ctrl', 'ctrl'] and ['ctrl'] and ['ctrl', 'ctrl', 'ctrl'] all reduce
    to the SAME frozenset ({'ctrl'}) under dedupe's set-based comparison, so
    dedupe must treat them as duplicates of each other."""
    d, r, c = hotkey_state.dedupe(["ctrl", "ctrl"], ["ctrl"], ["cmd", "alt"])
    assert d == ["ctrl", "ctrl"]
    assert r == Config().repaste_keys  # demoted: {'ctrl'} == {'ctrl'}
    assert c == ["cmd", "alt"]


# ============================================================================
# Section 10: very large key lists (>3) combined with the above
# ============================================================================


@pytest.mark.parametrize(
    "toml_value",
    [
        '["ctrl", "shift", "alt", "cmd"]',           # 4 distinct
        '["ctrl", "ctrl", "shift", "alt"]',           # 4 with a duplicate
        '["a", "b", "c", "d", "e"]',                   # 5, no modifier at all
    ],
)
def test_J1_config_toml_more_than_three_keys_raises(tmp_path, toml_value):
    """Pre-existing shape contract: config.toml keys lists over 3 items
    always raise ValueError, regardless of duplicates or modifier presence
    -- validate_keys's 1-3 count check runs before validate_combo."""
    p = tmp_path / "config.toml"
    p.write_text(f"[hotkey]\nkeys = {toml_value}\n")
    with pytest.raises(ValueError):
        load_config(str(p))


@pytest.mark.parametrize(
    "value",
    [
        ["ctrl", "shift", "alt", "cmd"],
        ["ctrl", "ctrl", "shift", "alt"],
        ["a", "b", "c", "d", "e"],
    ],
)
def test_J2_hotkeys_json_more_than_three_keys_falls_back_silently(tmp_path, value):
    """Same shape violation via hotkeys.json must NEVER raise -- resolve()
    falls back to the config default silently (not the loud
    validate_combo-rejection log, since this fails validate_keys's shape
    check first, same as any other malformed hotkeys.json shape)."""
    p = tmp_path / "hotkeys.json"
    p.write_text(json.dumps({"correct": value}))
    _, _, correct = hotkey_state.resolve(Config(), path=p)
    assert correct == ["cmd", "alt"]


# ============================================================================
# Section 11: dedupe() defensive/type robustness (lower priority)
# ============================================================================


def test_K1_dedupe_missing_argument_raises_typeerror():
    """dedupe(dictate, repaste, correct) is documented as exactly 3
    positional args; calling with fewer is an ordinary Python arity error,
    not something dedupe is expected to guard against."""
    with pytest.raises(TypeError):
        hotkey_state.dedupe(["ctrl", "shift"], ["cmd", "alt"])  # missing `correct`


def test_K2_dedupe_empty_lists_all_three():
    """Three empty lists are all the same (empty) set -- dedupe demotes the
    2nd and 3rd to their real defaults; dictate is left as [] since dedupe
    performs no shape validation of its own (that's resolve()'s job, and in
    the real pipeline resolve() never emits an empty list). Documents
    dedupe's actual, narrower contract: it trusts its inputs are already
    validated combos and only removes cross-combo collisions."""
    d, r, c = hotkey_state.dedupe([], [], [])
    assert d == []
    assert r == Config().repaste_keys
    assert c == Config().correct_keys


def test_K3_dedupe_string_argument_iterates_as_chars_not_rejected():
    """Passing a bare string (violating the documented list[str] type) is
    silently accepted and iterated character-by-character by
    frozenset(keys) -- "ctrl" becomes {'c','t','r','l'} -- rather than
    raising a clear TypeError/ValueError about the wrong argument type.
    Low-priority type-robustness gap: dedupe has no input validation of its
    own and none is strictly promised, but a bare string is a very easy
    caller mistake (e.g. forgetting to wrap a single combo in a list) to
    make silently wrong instead of loudly wrong."""
    d, r, c = hotkey_state.dedupe("ctrl", ["cmd", "alt"], ["cmd", "ctrl"])
    assert d == ["c", "t", "r", "l"], (
        f"documents silent char-splitting of a string argument, got {d!r}"
    )


def test_K4_dedupe_none_argument_raises_typeerror():
    """None is not iterable -- frozenset(None) raises TypeError. Documents
    that dedupe crashes (rather than defensively falling back) on a None
    combo; type hints promise list[str], so this is a caller-contract
    violation, not a hotkeys.json/config.toml-driven input (those always
    flow through resolve() first, which never emits None)."""
    with pytest.raises(TypeError):
        hotkey_state.dedupe(None, ["cmd", "alt"], ["cmd", "ctrl"])


def test_K5_dedupe_no_clash_three_distinct_combos_with_internal_dup_untouched():
    """Sanity/regression: three genuinely distinct combos (as sets) pass
    through dedupe completely unchanged, even when one has internal
    whitespace/case oddities that make it "distinct" only because nothing
    else collides with its literal frozenset."""
    result = hotkey_state.dedupe(["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])
    assert result == (["ctrl", "shift"], ["cmd", "ctrl"], ["cmd", "alt"])


# ============================================================================
# Section 12: additional resolve()/load_config regression coverage
# ============================================================================


def test_L1_resolve_valid_saved_combo_survives_dedupe_untouched(tmp_path):
    """Full happy-path pipeline sanity check: three legitimately distinct
    saved combos survive resolve() and dedupe() completely unchanged."""
    p = tmp_path / "hotkeys.json"
    hotkey_state.save(["cmd", "shift"], ["alt", "shift"], ["ctrl", "alt"], path=p)
    resolved = hotkey_state.resolve(Config(), path=p)
    result = hotkey_state.dedupe(*resolved)
    assert result == (["cmd", "shift"], ["alt", "shift"], ["ctrl", "alt"])


def test_L2_three_key_combo_with_modifier_and_char_accepted(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["cmd", "alt", "r"]\n')
    cfg = load_config(str(p))
    assert cfg.correct_keys == ["cmd", "alt", "r"]


def test_L3_three_key_combo_no_modifier_falls_back(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[correct]\nkeys = ["a", "b", "c"]\n')
    cfg = load_config(str(p))
    assert cfg.correct_keys == ["cmd", "alt"]
