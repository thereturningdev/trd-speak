from flow.corrector import TextCorrector
from flow.dictionary import Replacement

def mk(*pairs, **kw):
    return TextCorrector([Replacement(a, b, **kw) for a, b in pairs])

def test_whole_word_default_does_not_touch_substring():
    c = mk(("cat", "dog"))
    assert c.correct("the cat in category") == "the dog in category"

def test_whole_word_false_replaces_substring():
    c = TextCorrector([Replacement("cat", "dog", whole_word=False)])
    assert c.correct("category") == "dogegory"

def test_longest_from_wins():
    c = mk(("machine", "device"), ("machine learning", "ML"))
    assert c.correct("machine learning is machine work") == "ML is device work"

def test_case_insensitive_match_lowercase_target_mirrors_case():
    c = mk(("teh", "the"))
    assert c.correct("Teh start. teh end. TEH END") == "The start. the end. THE END"

def test_branded_target_is_verbatim_regardless_of_match_case():
    c = mk(("github", "GitHub"))
    assert c.correct("push to github and Github and GITHUB") == \
        "push to GitHub and GitHub and GitHub"

def test_case_sensitive_only_fires_on_matching_case():
    c = TextCorrector([Replacement("LocalFlow", "LocalFlow!", case_sensitive=True)])
    assert c.correct("localflow vs LocalFlow") == "localflow vs LocalFlow!"

def test_punctuation_adjacency_and_edges():
    c = mk(("github", "GitHub"))
    assert c.correct("github.") == "GitHub."
    assert c.correct("github") == "GitHub"

def test_no_cascade_between_rules():
    c = mk(("aa", "bb"), ("bb", "cc"))
    assert c.correct("aa bb") == "bb cc"

def test_unicode_word_boundary():
    c = mk(("eleve", "élève"))
    assert c.correct("the eleve") == "the élève"

def test_empty_rules_is_identity():
    c = TextCorrector([])
    assert c.correct("anything at all") == "anything at all"
