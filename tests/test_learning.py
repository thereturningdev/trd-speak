from flow.learning import derive

# Fake predicate: only these are "common" so tests don't depend on the word list.
COMMON = {"the", "code", "program", "cloud", "to", "a", "is"}


def is_common(w): return w.lower() in COMMON


def test_uncommon_single_word_swap_learns_rule_and_vocab():
    r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="t")
    assert [(x.from_, x.to, x.learned) for x in r.rules] == [("diotaleavy", "Diotalevi", True)]
    assert r.vocab == ["Diotalevi"]

def test_common_wrong_word_is_vocab_only_no_rule():
    r = derive("ask cloud now", "ask Claude now", is_common)
    assert r.rules == []
    assert r.vocab == ["Claude"]

def test_inserts_and_deletes_learn_nothing():
    assert derive("hello world", "hello there world", is_common).rules == []   # insert
    assert derive("hello there world", "hello world", is_common).rules == []   # delete

def test_multi_word_replace_is_skipped():
    r = derive("fast whisper rocks", "faster-whisper rocks", is_common)
    assert r.rules == []  # 2 words -> 1 word is not a 1:1 swap

def test_format_guard_rejects_too_short_or_digits():
    assert derive("i ran", "I ran", is_common).rules == []

def test_dedupe_keeps_first_per_wrong():
    r = derive("zzx and zzx", "Zed and Zedd", is_common)
    froms = [x.from_ for x in r.rules]
    assert froms.count("zzx") == 1


def test_two_distinct_uncommon_swaps_in_one_correction():
    # "diotaleavy" and "ctranslat" are both outside COMMON and differ from
    # their corrections by more than casing, so both should generate rules.
    # "and" is a connector that stays unchanged.
    r = derive(
        "call diotaleavy and ctranslat",
        "call Diotalevi and CTranslate",
        is_common,
    )
    froms = [x.from_ for x in r.rules]
    tos = [x.to for x in r.rules]
    assert "diotaleavy" in froms
    assert "ctranslat" in froms
    assert "Diotalevi" in tos
    assert "CTranslate" in tos
    assert "Diotalevi" in r.vocab
    assert "CTranslate" in r.vocab
