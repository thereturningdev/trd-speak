from flow.common_words import is_common


def test_everyday_words_are_common():
    for w in ("the", "cloud", "guitar", "code", "program"):
        assert is_common(w), w


def test_case_insensitive():
    assert is_common("Cloud") and is_common("THE")


def test_invented_mishearings_are_not_common():
    for w in ("diotaleavy", "ctranslate", "qwxzjk"):
        assert not is_common(w), w
