import json
import pytest
from flow.dictionary import Dictionary, Replacement, load_dictionary, save_dictionary

def test_missing_file_is_empty(tmp_path):
    d = load_dictionary(tmp_path / "nope.json")
    assert d == Dictionary(vocabulary=[], replacements=[])

def test_valid_file_parses_with_defaults(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({
        "vocabulary": ["GitHub", "Diotalevi"],
        "replacements": [
            {"from": "fast whisper", "to": "faster-whisper"},
            {"from": "diotaleavy", "to": "Diotalevi", "learned": True, "ts": "2026-06-25T10:00:00"},
        ],
    }))
    d = load_dictionary(p)
    assert d.vocabulary == ["GitHub", "Diotalevi"]
    r0, r1 = d.replacements
    assert (
        (r0.from_, r0.to, r0.case_sensitive, r0.whole_word, r0.learned)
        == ("fast whisper", "faster-whisper", False, True, False)
    )
    assert (r1.from_, r1.to, r1.learned, r1.ts) == ("diotaleavy", "Diotalevi", True, "2026-06-25T10:00:00")

def test_malformed_json_raises(tmp_path):
    p = tmp_path / "dictionary.json"; p.write_text("{not json")
    with pytest.raises(ValueError):
        load_dictionary(p)

def test_bad_types_raise(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({"replacements": [{"from": "", "to": "x"}]}))
    with pytest.raises(ValueError):
        load_dictionary(p)

def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "dictionary.json"
    p.write_text(json.dumps({"vocabulary": ["a"], "replacements": [], "extra": 1}))
    assert load_dictionary(p).vocabulary == ["a"]

def test_save_round_trips(tmp_path):
    p = tmp_path / "dictionary.json"
    d = Dictionary(vocabulary=["GitHub"],
                   replacements=[Replacement("diotaleavy", "Diotalevi", learned=True, ts="t")])
    save_dictionary(d, p)
    assert load_dictionary(p) == d
