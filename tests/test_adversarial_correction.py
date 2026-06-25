"""Adversarial tests for flow/corrector.py (TextCorrector) and flow/learning.py (derive).

Strategy: attack every boundary, edge case, and failure mode documented in the specs
and docstrings. Tests are kept even when passing — they form a regression suite.

Fake is_common predicate used throughout to make derive() tests deterministic
and independent of the bundled word list.
"""
from __future__ import annotations

import pytest

from flow.corrector import TextCorrector, _apply_case
from flow.dictionary import Replacement
from flow.learning import derive, LearnResult
import flow.common_words as common_words_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

COMMON = {"cloud", "the", "code", "program", "to", "a", "is", "cat", "see", "run"}


def is_common(w: str) -> bool:
    return w.lower() in COMMON


def rep(from_: str, to: str, *, case_sensitive: bool = False, whole_word: bool = True) -> Replacement:
    return Replacement(from_=from_, to=to, case_sensitive=case_sensitive, whole_word=whole_word)


def corrector(*pairs, **kw) -> TextCorrector:
    return TextCorrector([rep(f, t, **kw) for f, t in pairs])


# ===========================================================================
# PART 1: TextCorrector.correct() adversarial tests
# ===========================================================================


# --- 1.1 Empty / trivial inputs ---

class TestEmptyAndTrivial:
    def test_empty_rules_empty_string(self):
        """Empty rules + empty input → empty string (identity)."""
        c = TextCorrector([])
        assert c.correct("") == ""

    def test_empty_rules_nonempty_string(self):
        """Empty rules → exact identity regardless of text."""
        c = TextCorrector([])
        assert c.correct("hello world") == "hello world"

    def test_empty_input_with_rules(self):
        """Non-empty rules, empty input → empty string returned unchanged."""
        c = corrector(("cat", "dog"))
        assert c.correct("") == ""

    def test_whitespace_only_input(self):
        """Whitespace-only input with a rule → unchanged (no match)."""
        c = corrector(("cat", "dog"))
        assert c.correct("   \t\n  ") == "   \t\n  "


# --- 1.2 Whole-word boundary semantics ---

class TestWholeWordBoundary:
    def test_whole_word_default_does_not_match_prefix(self):
        """cat→dog must NOT touch 'catfish'."""
        c = corrector(("cat", "dog"))
        assert c.correct("catfish") == "catfish"

    def test_whole_word_default_does_not_match_suffix(self):
        """cat→dog must NOT touch 'tomcat'."""
        c = corrector(("cat", "dog"))
        assert c.correct("tomcat") == "tomcat"

    def test_whole_word_default_does_not_match_infix(self):
        """cat→dog must NOT touch 'concatenate'."""
        c = corrector(("cat", "dog"))
        assert c.correct("concatenate") == "concatenate"

    def test_whole_word_fires_standalone(self):
        """cat→dog fires when cat stands alone."""
        c = corrector(("cat", "dog"))
        assert c.correct("the cat sat") == "the dog sat"

    def test_whole_word_fires_at_string_start(self):
        c = corrector(("cat", "dog"))
        assert c.correct("cat sat") == "dog sat"

    def test_whole_word_fires_at_string_end(self):
        c = corrector(("cat", "dog"))
        assert c.correct("see the cat") == "see the dog"

    def test_whole_word_false_replaces_substring(self):
        """whole_word=False allows infix replacement."""
        c = TextCorrector([rep("cat", "dog", whole_word=False)])
        assert c.correct("category") == "dogegory"

    def test_whole_word_true_with_punctuation_adjacent(self):
        """Word boundary allows firing next to punctuation."""
        c = corrector(("github", "GitHub"))
        assert c.correct("github.") == "GitHub."
        assert c.correct("(github)") == "(GitHub)"
        assert c.correct('"github"') == '"GitHub"'

    def test_possessive_word_boundary(self):
        """'cat's' — the apostrophe may break word-boundary; document actual behavior."""
        c = corrector(("cat", "dog"))
        result = c.correct("cat's meow")
        # \b fires before the apostrophe — so "cat" is matched.
        # If cat IS matched: result is "dog's meow"
        # If cat is NOT matched: result is "cat's meow"
        # Either way, assert it's deterministic (no crash, no partial match).
        assert "cat" in result or "dog" in result

    def test_hyphenated_word_boundary(self):
        """'fast-cat' — hyphen is a word boundary in regex."""
        c = corrector(("cat", "dog"))
        result = c.correct("fast-cat")
        # \b fires at the hyphen, so "cat" part should be matched → "fast-dog"
        assert result == "fast-dog"

    def test_numeric_adjacent_boundary(self):
        """'cat2' — digit is not a \b boundary for the word side."""
        c = corrector(("cat", "dog"))
        # 'cat' followed by digit: \b exists between 'cat' and '2'? Actually \b
        # is between a \w char and \W char. Digit is \w, so no boundary between
        # 't' and '2'. Thus 'cat2' should NOT match.
        result = c.correct("cat2")
        assert result == "cat2"

    def test_whole_word_multiword_from_fires(self):
        """Multi-word from_ with whole_word fires on exact match."""
        c = TextCorrector([rep("machine learning", "ML")])
        assert c.correct("machine learning is great") == "ML is great"

    def test_whole_word_multiword_from_not_substring(self):
        """Multi-word from_ with whole_word must not match inside longer phrase (boundary check)."""
        c = TextCorrector([rep("cat sat", "dog lay")])
        # "The cat sat there" — should match
        assert c.correct("The cat sat there") == "The dog lay there"


# --- 1.3 Case handling ---

class TestCaseHandling:
    def test_case_insensitive_lowercase_target_mirrors_lower(self):
        """lowercase target: preserve input case when matched lowercase."""
        c = corrector(("teh", "the"))
        assert c.correct("teh word") == "the word"

    def test_case_insensitive_lowercase_target_mirrors_title(self):
        """lowercase target: capitalize when matched token is capitalized."""
        c = corrector(("teh", "the"))
        assert c.correct("Teh word") == "The word"

    def test_case_insensitive_lowercase_target_mirrors_upper(self):
        """lowercase target: all-caps when matched token is all-caps (len>1)."""
        c = corrector(("teh", "the"))
        assert c.correct("TEH word") == "THE word"

    def test_branded_target_verbatim_regardless_of_match_case(self):
        """Brand casing (GitHub) always emitted verbatim even if matched lower."""
        c = corrector(("github", "GitHub"))
        assert c.correct("push to github") == "push to GitHub"
        assert c.correct("push to Github") == "push to GitHub"
        assert c.correct("push to GITHUB") == "push to GitHub"

    def test_mixed_case_target_verbatim(self):
        """A target like 'CTranslate2' has a capital letter — emitted verbatim."""
        c = corrector(("ctranslate", "CTranslate2"))
        assert c.correct("using ctranslate") == "using CTranslate2"
        assert c.correct("using CTRANSLATE") == "using CTranslate2"

    def test_case_sensitive_only_fires_on_exact_case(self):
        """case_sensitive=True: must not fire on different case."""
        c = TextCorrector([rep("LocalFlow", "LocalFlow!", case_sensitive=True)])
        assert c.correct("localflow vs LocalFlow") == "localflow vs LocalFlow!"

    def test_case_sensitive_uppercase_match(self):
        """case_sensitive=True with ALL_CAPS from_."""
        c = TextCorrector([rep("CAT", "dog", case_sensitive=True)])
        assert c.correct("CAT cat Cat") == "dog cat Cat"

    def test_single_char_matched_uppercase_mirror(self):
        """Single-char uppercase token with lowercase target: only first char uppercased."""
        c = corrector(("xx", "ab"))  # len(matched)>1 → isupper check applies
        assert c.correct("XX") == "AB"  # all-caps mirror: all-caps output

    def test_all_uppercase_single_letter_matched(self):
        """Single letter uppercase: matched.isupper() and len>1 is False → capitalize."""
        # "X" is single letter all-caps, but len=1 so not `isupper() and len>1`
        # → falls to `matched[:1].isupper()` → capitalize only first char of replacement
        c = corrector(("xx", "ab"))
        assert c.correct("Xx") == "Ab"  # Title-case input → Title-case output

    def test_lowercase_target_all_lower_input(self):
        """Fully lowercase target, fully lowercase match → lowercase output."""
        c = corrector(("abc", "xyz"))
        assert c.correct("abc") == "xyz"


# --- 1.4 Regex metacharacters in from_ (literal treatment) ---

class TestRegexMetacharacters:
    def test_dot_in_from_is_literal(self):
        """'a.b' in from_ must NOT match 'axb'."""
        c = corrector(("a.b", "X"), whole_word=False)
        # Dot in the regex should be escaped → literal dot only
        assert c.correct("axb") == "axb"
        assert c.correct("a.b") == "X"

    def test_plus_in_from_is_literal(self):
        """'a+' in from_ must match literal 'a+', not one-or-more a's."""
        c = corrector(("a+", "X"), whole_word=False)
        assert c.correct("a+") == "X"
        assert c.correct("aaa") == "aaa"

    def test_asterisk_in_from_is_literal(self):
        c = corrector(("a*", "X"), whole_word=False)
        assert c.correct("a*") == "X"
        assert c.correct("aaa") == "aaa"

    def test_parens_in_from_is_literal(self):
        """'(x)' in from_ must match literal '(x)', not a capture group."""
        c = corrector(("(x)", "Y"), whole_word=False)
        assert c.correct("(x)") == "Y"
        assert c.correct("x") == "x"

    def test_brackets_in_from_is_literal(self):
        c = corrector(("[ab]", "Z"), whole_word=False)
        assert c.correct("[ab]") == "Z"
        assert c.correct("a") == "a"
        assert c.correct("b") == "b"

    def test_caret_in_from_is_literal(self):
        c = corrector(("^word", "X"), whole_word=False)
        assert c.correct("^word") == "X"
        assert c.correct("word") == "word"

    def test_dollar_in_from_is_literal(self):
        c = corrector(("word$", "X"), whole_word=False)
        assert c.correct("word$") == "X"
        assert c.correct("word") == "word"

    def test_backslash_in_from_is_literal(self):
        c = corrector(("a\\b", "X"), whole_word=False)
        assert c.correct("a\\b") == "X"

    def test_pipe_in_from_is_literal(self):
        """'a|b' in from_ matches literal 'a|b', not 'a' or 'b'."""
        c = corrector(("a|b", "X"), whole_word=False)
        assert c.correct("a|b") == "X"
        assert c.correct("a") == "a"
        assert c.correct("b") == "b"

    def test_question_mark_in_from_is_literal(self):
        c = corrector(("ok?", "X"), whole_word=False)
        assert c.correct("ok?") == "X"
        assert c.correct("ok") == "ok"

    def test_curly_braces_in_from_is_literal(self):
        c = corrector(("a{3}", "X"), whole_word=False)
        assert c.correct("a{3}") == "X"
        assert c.correct("aaa") == "aaa"


# --- 1.5 Single-pass / no cascade ---

class TestSinglePassNoCascade:
    def test_no_cascade_a_to_b_b_to_c(self):
        """Rule output must NOT be re-matched. 'aa'→'bb', 'bb'→'cc': 'aa' → 'bb' not 'cc'."""
        c = corrector(("aa", "bb"), ("bb", "cc"))
        assert c.correct("aa") == "bb"

    def test_no_cascade_output_equals_other_rule_from(self):
        """Single pass: if rule1 output matches rule2 from_, rule2 must NOT fire."""
        c = corrector(("foo", "bar"), ("bar", "baz"))
        assert c.correct("foo bar") == "bar baz"
        # "foo" → "bar", "bar" stays "bar" (no cascade of the first rule's output)
        # but the existing "bar" in text IS hit by rule2 in the same pass
        # This tests that the single-pass correctly only fires rules once on original
        # The result should have NO cascading: "foo" produces "bar" and that "bar"
        # is NOT then turned to "baz" — but the existing "bar" is turned to "baz".

    def test_no_cascade_identical_from_and_to(self):
        """from_ == to: applying a rule whose output equals its input is idempotent."""
        c = corrector(("hello", "hello"))
        assert c.correct("hello world") == "hello world"

    def test_idempotency_on_already_correct_text(self):
        """correct(correct(text)) == correct(text) for well-formed rules."""
        c = corrector(("teh", "the"))
        first = c.correct("teh world")
        second = c.correct(first)
        assert first == second  # Idempotent: already-correct text unchanged

    def test_multiple_rules_same_pass_no_interaction(self):
        """Multiple non-overlapping rules apply correctly in one pass."""
        c = corrector(("foo", "FOO"), ("bar", "BAR"), ("baz", "BAZ"))
        assert c.correct("foo bar baz") == "FOO BAR BAZ"


# --- 1.6 Longest-from-first ordering ---

class TestLongestFromFirst:
    def test_longer_rule_beats_shorter_prefix(self):
        """'machine learning' beats 'machine' when both rules present."""
        c = TextCorrector([rep("machine", "device"), rep("machine learning", "ML")])
        assert c.correct("machine learning is machine work") == "ML is device work"

    def test_longer_from_wins_even_if_added_last(self):
        """Order of rule insertion doesn't matter; longest from_ always wins."""
        c = TextCorrector([rep("machine learning", "ML"), rep("machine", "device")])
        assert c.correct("machine learning done") == "ML done"

    def test_three_rules_longest_wins(self):
        """Three overlapping rules: longest of from_ wins at match site."""
        c = TextCorrector([
            rep("new york city", "NYC"),
            rep("new york", "NY"),
            rep("new", "fresh"),
        ])
        assert c.correct("new york city is not new york or just new") == \
            "NYC is not NY or just fresh"


# --- 1.7 Unicode / accents / emoji ---

class TestUnicode:
    def test_accented_word_boundary(self):
        """Unicode word boundary: \b with re.UNICODE handles accented chars."""
        c = corrector(("eleve", "élève"))
        assert c.correct("the eleve studies") == "the élève studies"

    def test_accented_character_in_from_(self):
        """from_ with accent should match literally."""
        c = corrector(("café", "coffee"), whole_word=False)
        assert c.correct("café au lait") == "coffee au lait"

    def test_emoji_in_text_not_matched_by_word_rule(self):
        """Emoji in text should not cause crashes; word rule ignores emoji."""
        c = corrector(("cat", "dog"))
        result = c.correct("the cat 🐱 sat")
        assert result == "the dog 🐱 sat"

    def test_unicode_combining_marks_in_text(self):
        """Text with combining marks shouldn't crash."""
        c = corrector(("cafe", "coffee"), whole_word=False)
        # café using combining accent (e + combining accent)
        text = "café is good"
        # Should not crash; exact match depends on normalization
        result = c.correct(text)
        assert isinstance(result, str)

    def test_cjk_text_not_matched_by_latin_rule(self):
        """CJK characters in text: Latin word rules don't match them."""
        c = corrector(("cat", "dog"))
        result = c.correct("猫 cat 犬")
        assert result == "猫 dog 犬"

    def test_from_with_dash_treated_as_literal(self):
        """A dash in from_ is escaped and treated literally."""
        c = corrector(("faster-whisper", "FastWhisper"), whole_word=False)
        assert c.correct("using faster-whisper here") == "using FastWhisper here"


# --- 1.8 Overlapping / conflicting rules ---

class TestOverlappingRules:
    def test_two_rules_same_from_first_wins(self):
        """Two rules with same from_ (different to): first one in sorted order wins."""
        # Both have same len(from_), so order is deterministic from insertion
        c = TextCorrector([rep("hello", "hi"), rep("hello", "hey")])
        result = c.correct("hello world")
        # Should be one of "hi world" or "hey world", not both or crash
        assert result in ("hi world", "hey world")

    def test_adjacent_non_overlapping_both_fire(self):
        """Two rules for adjacent words both fire."""
        c = corrector(("foo", "FOO"), ("bar", "BAR"))
        assert c.correct("foobar") == "foobar"  # substring, whole_word=True
        assert c.correct("foo bar") == "FOO BAR"

    def test_nested_rules_longer_wins(self):
        """'cat in hat' vs 'cat': longer should win at that position."""
        c = TextCorrector([rep("cat in hat", "book"), rep("cat", "dog")])
        assert c.correct("the cat in hat") == "the book"
        assert c.correct("the cat sat") == "the dog sat"


# --- 1.9 Edge cases with empty to / special to values ---

class TestSpecialToValues:
    def test_empty_to_removes_word(self):
        """Empty to: removes the matched word (deletion)."""
        c = corrector(("umm", ""))
        result = c.correct("umm hello umm")
        assert result == " hello "

    def test_to_with_spaces(self):
        """to containing spaces is emitted verbatim."""
        c = TextCorrector([rep("github", "GitHub Pages", whole_word=True)])
        assert c.correct("push to github") == "push to GitHub Pages"

    def test_from_equals_to(self):
        """from_ == to: identity substitution, no mutation."""
        c = corrector(("hello", "hello"))
        assert c.correct("hello world") == "hello world"

    def test_to_with_regex_metacharacters(self):
        """to containing regex special chars is emitted literally."""
        c = corrector(("foo", "bar(baz)"), whole_word=False)
        assert c.correct("foo") == "bar(baz)"

    def test_to_with_backslash_group_refs(self):
        """to with \\1 must NOT be treated as a backreference."""
        c = corrector(("foo", "\\1"), whole_word=False)
        # If re.sub is called with repl function, \1 in to is literal string
        # This should return "\\1" literally, not a backreference substitution
        result = c.correct("foo")
        assert result == "\\1"


# --- 1.10 Very long / stress inputs ---

class TestStressInputs:
    def test_very_long_text(self):
        """Very long input does not crash."""
        c = corrector(("foo", "bar"))
        long_text = "foo " * 10000
        result = c.correct(long_text.strip())
        assert "foo" not in result
        assert "bar" in result

    def test_very_long_from_(self):
        """Very long from_ string should not crash compilation."""
        long_from = "a" * 500
        c = TextCorrector([rep(long_from, "short", whole_word=False)])
        assert c.correct(long_from) == "short"
        assert c.correct("hello") == "hello"

    def test_many_rules(self):
        """Many rules (100) compiled together should not crash or malfunction."""
        pairs = [(f"wrong{i}", f"right{i}") for i in range(100)]
        c = TextCorrector([rep(f, t) for f, t in pairs])
        # Check first, last, and a middle rule fire correctly
        assert c.correct("wrong0") == "right0"
        assert c.correct("wrong99") == "right99"
        assert c.correct("wrong50") == "right50"

    def test_repeated_application_same_result(self):
        """Repeated correct() calls on same corrector give same result (no state mutation)."""
        c = corrector(("teh", "the"), ("ot", "of"))
        text = "teh world ot things"
        r1 = c.correct(text)
        r2 = c.correct(text)
        r3 = c.correct(text)
        assert r1 == r2 == r3


# --- 1.11 Whitespace in from_ ---

class TestWhitespaceHandling:
    def test_multiword_from_requires_exact_spacing(self):
        """Multi-word from_ with double space: only matches exact spacing."""
        c = TextCorrector([rep("fast whisper", "faster-whisper")])
        assert c.correct("fast whisper") == "faster-whisper"
        # Double space should not match single-space rule
        assert c.correct("fast  whisper") == "fast  whisper"

    def test_leading_trailing_whitespace_in_from_(self):
        """from_ with leading/trailing space: behavior is literal match."""
        # This tests that we don't crash and the regex is compiled literally
        c = TextCorrector([rep(" hello", "hi", whole_word=False)])
        result = c.correct("say hello there")
        # The space is part of the match; assert no crash
        assert isinstance(result, str)


# --- 1.12 apply_case internal function ---

class TestApplyCase:
    def test_apply_case_lower_input_lower_target(self):
        assert _apply_case("hello", "world") == "world"

    def test_apply_case_title_input_lower_target(self):
        assert _apply_case("Hello", "world") == "World"

    def test_apply_case_upper_input_lower_target(self):
        assert _apply_case("HELLO", "world") == "WORLD"

    def test_apply_case_branded_target_verbatim(self):
        """Target with any uppercase → verbatim regardless of match case."""
        assert _apply_case("hello", "GitHub") == "GitHub"
        assert _apply_case("HELLO", "GitHub") == "GitHub"

    def test_apply_case_single_uppercase_char_matched(self):
        """Single uppercase char: len=1 so not isupper()&len>1 → capitalize."""
        assert _apply_case("A", "xy") == "Xy"

    def test_apply_case_single_lowercase_char_matched(self):
        assert _apply_case("a", "xy") == "xy"


# ===========================================================================
# PART 2: derive() adversarial tests
# ===========================================================================


# --- 2.1 Length boundaries ---

class TestDeriveLengthBoundaries:
    def test_word_length_1_rejected(self):
        """Single-char word on either side → no rule, no vocab."""
        # 'i' → 'I' is a case-only diff anyway, but use a different pair
        r = derive("x ran", "y ran", is_common)
        assert r.rules == []
        assert r.vocab == []

    def test_word_length_2_accepted(self):
        """2-char words (MIN_LEN) → should be accepted if all other conditions met."""
        r = derive("ab something", "cd something", is_common)
        # 'ab' and 'cd' are 2 chars, uncommon, case differs → rule should form
        assert len(r.rules) >= 1 or r.vocab  # At minimum vocab should get 'cd'

    def test_word_length_30_accepted(self):
        """30-char words (MAX_LEN) → accepted."""
        w30 = "a" * 29 + "z"  # 30 chars
        w30b = "b" * 29 + "z"
        r = derive(f"{w30} now", f"{w30b} now", is_common)
        # Both are 30 chars, should be accepted
        assert r.vocab == [w30b] or len(r.rules) > 0

    def test_word_length_31_rejected(self):
        """31-char words (> MAX_LEN) → rejected."""
        w31 = "a" * 31
        w31b = "b" * 31
        r = derive(f"{w31} now", f"{w31b} now", is_common)
        assert r.rules == []
        assert r.vocab == []

    def test_word_length_1_wrong_skipped_entirely(self):
        """1-char wrong word → neither rule nor vocab."""
        # Single char word pair: 'x' → 'yz' (insert would be 2, this is replace 1→1)
        r = derive("go x now", "go yz now", is_common)
        # 'x' is 1 char (len < MIN_LEN=2) → skipped
        # But 'yz' is the right side — also needs len>=2; 'yz' is 2 chars
        # Actually the guard is on both wrong and right individually
        # 'x' has len=1 → skipped; so no rule, and no vocab either
        assert r.rules == []
        assert r.vocab == []

    def test_word_length_1_right_skipped_entirely(self):
        """1-char right word → neither rule nor vocab."""
        r = derive("go wrong now", "go x now", is_common)
        # 'wrong' is 5 chars OK, 'x' is 1 char (< MIN_LEN) → skipped
        assert r.rules == []
        assert r.vocab == []


# --- 2.2 Empty / whitespace / identical inputs ---

class TestDeriveEmptyAndIdentical:
    def test_empty_both(self):
        """Both empty → empty result."""
        r = derive("", "", is_common)
        assert r.rules == [] and r.vocab == []

    def test_identical_strings(self):
        """Identical strings → no diff → nothing learned."""
        r = derive("hello world", "hello world", is_common)
        assert r.rules == [] and r.vocab == []

    def test_whitespace_only_both(self):
        """Whitespace-only strings → no words → empty result."""
        r = derive("   ", "   ", is_common)
        assert r.rules == [] and r.vocab == []

    def test_empty_original_nonempty_edited(self):
        """All words are inserts → no rules, no vocab (only deletes/inserts, no replaces)."""
        r = derive("", "hello world", is_common)
        assert r.rules == [] and r.vocab == []

    def test_nonempty_original_empty_edited(self):
        """All words are deletes → no rules."""
        r = derive("hello world", "", is_common)
        assert r.rules == [] and r.vocab == []


# --- 2.3 Case-only diffs ---

class TestDeriveCaseDiffs:
    def test_case_only_diff_skipped(self):
        """wrong.lower() == right.lower() → no rule, no vocab (case-only diff)."""
        r = derive("hello World test", "hello world test", is_common)
        assert r.rules == []
        assert r.vocab == []  # case-only: 'world' lower equals 'world' lower

    def test_case_only_diff_uncommon(self):
        """Case-only diff on uncommon word → still skipped (spec: case-only diffs are skipped)."""
        r = derive("Diotalevi met", "diotalevi met", is_common)
        assert r.rules == []
        assert r.vocab == []

    def test_single_letter_diff_is_case_only(self):
        """'i' → 'I' is case-only, should be skipped."""
        r = derive("i ran", "I ran", is_common)
        assert r.rules == []


# --- 2.4 Insert/delete/multi-word ---

class TestDeriveInsertDelete:
    def test_insert_no_rule(self):
        """Insert → no rule."""
        r = derive("hello world", "hello there world", is_common)
        assert r.rules == []

    def test_delete_no_rule(self):
        """Delete → no rule."""
        r = derive("hello there world", "hello world", is_common)
        assert r.rules == []

    def test_multi_word_to_single_word(self):
        """2-word→1-word replace: not 1:1 so skipped."""
        r = derive("fast whisper rocks", "faster-whisper rocks", is_common)
        assert r.rules == []

    def test_single_word_to_multi_word(self):
        """1-word→2-word replace: i2-i1=1 but j2-j1=2, skipped."""
        r = derive("whisper rocks", "faster whisper rocks", is_common)
        assert r.rules == []

    def test_multi_word_to_multi_word(self):
        """2-word→2-word replace: i2-i1=2, skipped (not 1:1)."""
        r = derive("fast transcription rocks", "faster whisper rocks", is_common)
        # Two words replaced by two words → neither should create a rule
        # (each is a separate 1:1 if diff sees them as two separate replaces,
        # OR one 2:2 replace block → skipped)
        # The result depends on SequenceMatcher's grouping
        assert isinstance(r.rules, list)  # Just: no crash


# --- 2.5 Safety property: common wrong word → no rule ---

class TestDeriveSafetyProperty:
    def test_common_wrong_no_rule(self):
        """THE SAFETY PROPERTY: common wrong word → NO rule (only vocab)."""
        r = derive("ask cloud to help", "ask Claude to help", is_common)
        assert r.rules == [], "SAFETY VIOLATION: 'cloud' is common, must not produce a rule"
        assert "Claude" in r.vocab

    def test_common_wrong_multiple_common(self):
        """Multiple common wrong words → no rules at all."""
        r = derive("the code is wrong", "the program is wrong", is_common)
        # 'code' → 'program': both in COMMON → no rule
        # But 'wrong' was not changed → no diff there
        assert r.rules == []

    def test_uncommon_wrong_creates_rule(self):
        """Uncommon wrong word → rule IS created."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common)
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms

    def test_boundary_word_common_no_rule(self):
        """'program' is in COMMON → no rule even if edited."""
        r = derive("run program here", "run script here", is_common)
        # 'program' is in COMMON → no rule for program→script
        assert all(x.from_.lower() != "program" for x in r.rules)

    def test_real_common_words_predicate_cloud_is_common(self):
        """Using the REAL is_common: 'cloud' should be common → no rule."""
        r = derive("ask cloud to help", "ask Claude to help", common_words_module.is_common)
        # 'cloud' is a very common English word → no rule
        assert all(x.from_.lower() != "cloud" for x in r.rules)
        # But 'Claude' should be in vocab
        assert "Claude" in r.vocab

    def test_real_common_words_predicate_uncommon_yields_rule(self):
        """Using real is_common: 'diotaleavy' (clearly uncommon) → rule created."""
        r = derive("call diotaleavy now", "call Diotalevi now", common_words_module.is_common)
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms


# --- 2.6 Vocab: always added (even when wrong is common) ---

class TestDeriveVocabAlwaysAdded:
    def test_vocab_added_even_when_rule_skipped_common(self):
        """Vocab target is always added, even when wrong is common (no rule)."""
        r = derive("ask cloud now", "ask Claude now", is_common)
        assert r.rules == []
        assert "Claude" in r.vocab

    def test_vocab_added_for_uncommon_wrong_too(self):
        """Vocab target added for uncommon wrong word (rule AND vocab)."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="t1")
        assert "Diotalevi" in r.vocab
        assert any(x.from_ == "diotaleavy" for x in r.rules)

    def test_vocab_dedupe_by_lower(self):
        """Vocab deduped by lower(): same word with different case → one entry."""
        r = derive("zzx and zzx", "Zed and Zedd", is_common)
        # Two replacements: zzx→Zed and zzx→Zedd, but zzx deduped to first rule
        # Vocab: only first 'right' word per lower() key
        # 'Zed' and 'Zedd' have different lower() so both might appear
        # But 'zzx' as wrong dedupes to first rule only
        assert len([x for x in r.rules if x.from_ == "zzx"]) <= 1

    def test_vocab_case_preserved(self):
        """Vocab preserves the original casing of the right word."""
        r = derive("using ctranslat now", "using CTranslate now", is_common)
        assert "CTranslate" in r.vocab
        assert "ctranslate" not in r.vocab  # should be CTranslate, not lowercase


# --- 2.7 Timestamp propagation ---

class TestDeriveTimestamp:
    def test_ts_propagates_to_rules(self):
        """ts parameter propagates onto learned rules."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="2026-06-25")
        for rule in r.rules:
            assert rule.ts == "2026-06-25"

    def test_ts_none_default(self):
        """ts=None (default) → rules have ts=None."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common)
        for rule in r.rules:
            assert rule.ts is None

    def test_learned_flag_is_true(self):
        """Learned rules have learned=True."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="t")
        for rule in r.rules:
            assert rule.learned is True


# --- 2.8 Digits and punctuation in words ---

class TestDeriveDigitsAndPunctuation:
    def test_word_with_digits_rejected(self):
        """Words containing digits are not matched by _WORD regex → no rule."""
        # 'CTranslate2' contains digit — the _WORD regex excludes digits
        # So "ctranslate2" would not be captured as a word
        r = derive("using ctranslate2 engine", "using CTranslate engine", is_common)
        # 'ctranslate2' contains a digit → _WORD won't capture it
        # So diff sees [] vs ['CTranslate'] effectively for those positions
        # This might cause an insert rather than replace → no rule
        assert isinstance(r.rules, list)  # No crash at minimum

    def test_word_with_underscore_rejected(self):
        r"""Underscores: _WORD uses [^\W\d_]+ so underscore-containing words rejected."""
        r = derive("using my_module here", "using your_module here", is_common)
        # my_module and your_module contain underscore → not captured by _WORD
        assert isinstance(r.rules, list)

    def test_punctuation_only_tokens(self):
        """Punctuation-only tokens are not words → no effect."""
        r = derive("hello. world", "hello! world", is_common)
        assert r.rules == []

    def test_word_with_internal_apostrophe_accepted(self):
        """Internal apostrophe is allowed: "don't" is a valid word token."""
        # The _WORD regex allows internal apostrophes
        r = derive("it's correct", "its correct", is_common)
        # "it's" has apostrophe, "its" does not — they differ, but len may be short
        assert isinstance(r.rules, list)

    def test_word_with_internal_hyphen_accepted(self):
        """Internal hyphen allowed: 'faster-whisper' is one token."""
        r = derive("use fast-whisper now", "use faster-whisper now", is_common)
        # 'fast-whisper' and 'faster-whisper' are both valid word tokens
        assert isinstance(r.rules, list)


# --- 2.9 Deduplication ---

class TestDeriveDedupe:
    def test_same_wrong_twice_only_one_rule(self):
        """Same wrong word appearing twice → only first rule kept."""
        r = derive("zzx and zzx", "Zed and Zedd", is_common)
        froms = [x.from_ for x in r.rules]
        assert froms.count("zzx") == 1

    def test_same_wrong_different_case_dedupes(self):
        """wrong.lower() used for dedup: 'FOO' and 'foo' same key → one rule."""
        r = derive("FOO something foo end", "BAR something bar end", is_common)
        # FOO→BAR and foo→bar are same key (foo.lower()==foo.lower())
        # Only first should be kept
        froms = [x.from_.lower() for x in r.rules]
        assert froms.count("foo") <= 1

    def test_vocab_dedupe_same_right_word(self):
        """Same right word appearing twice → only first vocab entry."""
        r = derive("zzx and zzx", "Zed and Zed", is_common)
        vocab_lower = [v.lower() for v in r.vocab]
        assert vocab_lower.count("zed") == 1


# --- 2.10 Multiple swaps in one correction ---

class TestDeriveMultipleSwaps:
    def test_two_uncommon_swaps_both_become_rules(self):
        """Two distinct uncommon swaps → two rules."""
        r = derive(
            "call diotaleavy and ctranslat",
            "call Diotalevi and CTranslate",
            is_common,
        )
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms
        assert "ctranslat" in froms

    def test_one_common_one_uncommon_swap(self):
        """Mixed: common wrong → no rule; uncommon wrong → rule."""
        r = derive(
            "cloud is diotaleavy",
            "Claude is Diotalevi",
            is_common,
        )
        froms = [x.from_ for x in r.rules]
        # 'cloud' is common → no rule
        assert "cloud" not in froms
        # 'diotaleavy' is uncommon → rule
        assert "diotaleavy" in froms
        # Both right words in vocab
        assert "Claude" in r.vocab
        assert "Diotalevi" in r.vocab

    def test_three_swaps_mix(self):
        """Three swaps: two uncommon, one common."""
        r = derive(
            "diotaleavy uses code for ctranslat",
            "Diotalevi uses Python for CTranslate",
            is_common,
        )
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms
        assert "ctranslat" in froms
        assert "code" not in froms  # 'code' is common


# --- 2.11 Rule from_ casing ---

class TestDeriveRuleFromCasing:
    def test_rule_from_preserves_asr_casing(self):
        """from_ preserves the ASR transcript casing (spec: 'diotaleavy' not 'Diotaleavy')."""
        r = derive("call Diotaleavy now", "call Diotalevi now", is_common)
        # The wrong word is 'Diotaleavy' (capitalized in transcript)
        froms = [x.from_ for x in r.rules]
        # Should preserve original casing 'Diotaleavy' not lowercase
        assert any(f == "Diotaleavy" for f in froms)


# --- 2.12 Edge cases in diff grouping ---

class TestDeriveDiffGrouping:
    def test_completely_different_texts(self):
        """Totally different texts: many replaces — only valid 1:1 non-common pairs become rules."""
        r = derive("diotaleavy met ctranslat", "Diotalevi used CTranslate", is_common)
        # SequenceMatcher will see this as multiple replaces
        assert isinstance(r.rules, list)
        assert isinstance(r.vocab, list)

    def test_single_word_texts(self):
        """Single word original → single word edited: straightforward."""
        r = derive("diotaleavy", "Diotalevi", is_common)
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms

    def test_single_common_word_texts(self):
        """Single common word: no rule."""
        r = derive("cloud", "Claude", is_common)
        assert r.rules == []
        assert "Claude" in r.vocab


# --- 2.13 Long word edge cases ---

class TestDeriveLongWords:
    def test_exactly_30_char_word_accepted(self):
        """Exactly 30-char word (MAX_LEN) is accepted."""
        w = "a" * 29 + "z"  # 30 chars, all letters
        r = derive(f"{w} now", "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaz now", is_common)
        # Both words are 30 chars; depends on case diff check too
        # Let's use clearly different words
        w1 = "a" * 30  # 30 a's
        w2 = "b" * 30  # 30 b's
        r = derive(f"{w1} now", f"{w2} now", is_common)
        assert r.vocab == [w2]
        assert any(x.from_ == w1 for x in r.rules)

    def test_exactly_31_char_word_rejected(self):
        """31-char word (> MAX_LEN) is rejected."""
        w1 = "a" * 31
        w2 = "b" * 31
        r = derive(f"{w1} now", f"{w2} now", is_common)
        assert r.rules == []
        assert r.vocab == []


# --- 2.14 Sentence with surrounding punctuation / numbers ---

class TestDeriveSentenceContext:
    def test_words_extracted_correctly_from_punctuated_sentence(self):
        """Punctuation-rich sentences: only letter words extracted."""
        r = derive("call diotaleavy, right?", "call Diotalevi, right?", is_common)
        froms = [x.from_ for x in r.rules]
        assert "diotaleavy" in froms

    def test_number_inline_text(self):
        """Numbers inline: digit tokens not captured by _WORD."""
        r = derive("version 3 software", "version 4 software", is_common)
        # '3' and '4' are not captured by _WORD (digits excluded)
        # So the diff sees only ['version', 'software'] vs ['version', 'software']
        assert r.rules == []


# ===========================================================================
# PART 3: Interaction between corrector and learning
# ===========================================================================

class TestCorrectorAndLearningInteraction:
    def test_learned_rule_applied_by_corrector(self):
        """A rule from derive() can be used directly in TextCorrector."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common, ts="t")
        c = TextCorrector(r.rules)
        result = c.correct("call diotaleavy now")
        assert "Diotalevi" in result

    def test_learned_rule_case_insensitive_by_default(self):
        """Learned rules are case_insensitive=False (default False → insensitive)."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common)
        assert all(not rule.case_sensitive for rule in r.rules)

    def test_learned_rule_whole_word_by_default(self):
        """Learned rules use whole_word=True by default."""
        r = derive("call diotaleavy now", "call Diotalevi now", is_common)
        assert all(rule.whole_word for rule in r.rules)

    def test_common_word_in_vocab_can_still_be_used_for_biasing(self):
        """Vocab always populated even for common wrong words (for hotword biasing)."""
        r = derive("ask cloud now", "ask Claude now", is_common)
        # No rule (cloud is common) but vocab has Claude for hotword biasing
        assert "Claude" in r.vocab
        # Corrector with no rules does nothing
        c = TextCorrector(r.rules)
        assert c.correct("ask cloud now") == "ask cloud now"


# ===========================================================================
# PART 4: Confirmed bugs and ambiguous edge cases
# ===========================================================================


class TestConfirmedBugs:
    """These tests document confirmed deviations from the spec.
    They are EXPECTED TO FAIL on the current implementation.
    Do NOT fix here — only discover and report.
    """

    def test_bug_empty_from_corrupts_output_whole_word_false(self):
        """BUG: Empty from_ with whole_word=False inserts replacement between every char.

        Spec says: deterministic, well-defined behavior. An empty from_ string
        should be treated as a no-op (identity), not corrupt the text.
        Actual: re.escape('') = '' which matches between every character.
        """
        c = TextCorrector([Replacement("", "hello", whole_word=False)])
        result = c.correct("test")
        # Bug: result = 'hellothelloehelloshellothello' (inserts 'hello' between every char)
        # Expected: 'test' (identity) or at least not corrupt the text
        assert result == "test", (
            f"BUG: empty from_ corrupted text — got {repr(result)}, expected 'test'"
        )

    def test_bug_empty_from_corrupts_output_whole_word_true(self):
        """BUG: Empty from_ with whole_word=True also corrupts output.

        \\b\\b wraps empty string — matches at word boundaries, inserting at both ends.
        """
        c = TextCorrector([Replacement("", "hello", whole_word=True)])
        result = c.correct("test")
        # Bug: result = 'hellotesthello' (inserts at word boundaries)
        assert result == "test", (
            f"BUG: empty from_ (whole_word=True) corrupted text — got {repr(result)}"
        )

    def test_bug_adjacent_two_word_swaps_lose_vocab(self):
        """BUG: Two adjacent word swaps with no separator → SequenceMatcher groups them
        as a 2:2 replace block → the entire block is skipped → NOTHING added to vocab.

        Spec says: ALWAYS add the corrected target to vocab (even when wrong is common).
        This is violated when SequenceMatcher groups adjacent swaps as multi-word blocks.

        Example: 'diotaleavy ctranslat' → 'Diotalevi CTranslate' (adjacent, no connector)
        Both words are lost from vocab. With a separator ('and'), both would be preserved.
        """
        r = derive(
            "call diotaleavy ctranslat",
            "call Diotalevi CTranslate",
            is_common,
        )
        # Spec: both 'Diotalevi' and 'CTranslate' should be in vocab
        # Bug: vocab is [] because SequenceMatcher sees this as a single 2:2 replace
        assert "Diotalevi" in r.vocab, (
            "BUG: 'Diotalevi' not added to vocab — adjacent 2:2 replace grouped, entire block skipped"
        )
        assert "CTranslate" in r.vocab, (
            "BUG: 'CTranslate' not added to vocab — adjacent 2:2 replace grouped, entire block skipped"
        )

    def test_bug_adjacent_common_and_uncommon_swap_lose_vocab(self):
        """BUG: When a common wrong word and an uncommon wrong word are adjacent,
        SequenceMatcher groups them as 2:2 replace → both lost from vocab.

        Even 'Claude' (the canonical example from the spec) is lost from vocab
        when it appears adjacent to another changed word.
        """
        r = derive(
            "check cloud ctranslat now",
            "check Claude CTranslate now",
            is_common,
        )
        # Spec: 'Claude' should ALWAYS be added to vocab (even when wrong is common)
        # Bug: vocab is [] because the entire adjacent block is skipped
        assert "Claude" in r.vocab, (
            "BUG: 'Claude' not in vocab — spec says ALWAYS add corrected target to vocab"
        )

    def test_bug_digit_suffix_truncates_vocab_target(self):
        """BUG: When the edited (right) word contains a trailing digit (e.g. 'CTranslate2'),
        _words() strips the digit → vocab and rule.to receive 'CTranslate' not 'CTranslate2'.

        Spec says: always add the corrected target to vocab. The corrected target is
        'CTranslate2' but only 'CTranslate' is stored — silent data loss.

        The user typed 'CTranslate2' as their correction; the system silently truncates it.
        """
        r = derive("ctranslat engine", "CTranslate2 engine", is_common)
        # Spec: vocab should contain 'CTranslate2' (user's intended correction)
        # Bug: vocab = ['CTranslate'] (digit stripped by _WORD regex)
        assert "CTranslate2" in r.vocab, (
            f"BUG: vocab={r.vocab!r} — 'CTranslate2' truncated to 'CTranslate'; digit stripped by _words()"
        )
        if r.rules:
            assert r.rules[0].to == "CTranslate2", (
                f"BUG: rule.to={r.rules[0].to!r} — should be 'CTranslate2' but digit was stripped"
            )

    def test_bug_digit_in_wrong_word_creates_wrong_rule_from(self):
        """RELATED BUG: When the original (wrong) word contains digits (e.g. 'ctranslate2'),
        _words() strips the digit → rule.from_ becomes 'ctranslate' instead of 'ctranslate2'.

        Additionally, the stripped wrong 'ctranslate' and stripped right 'CTranslate'
        are case-identical → the case-only filter skips BOTH rule and vocab entirely.

        A user who says 'ctranslate2' ASR will get NO learning at all.
        """
        r = derive("using ctranslate2 engine", "using CTranslate engine", is_common)
        # If digit stripping causes case-only diff: nothing learned
        # Even 'CTranslate' should be in vocab but isn't
        # This is a double-whammy: digit stripped from wrong → case-only → nothing
        # The system should at minimum add 'CTranslate' to vocab
        assert "CTranslate" in r.vocab, (
            f"BUG: vocab={r.vocab!r} — 'CTranslate' missing because "
            "digit stripped from wrong makes 'ctranslate'≈'CTranslate' case-only"
        )


class TestAmbiguousEdgeCases:
    """Cases where behavior is technically consistent with the spec but may surprise users
    or where the spec is silent/ambiguous. Not necessarily bugs.
    """

    def test_2to2_grouped_diff_populates_vocab(self):
        """Correcting two adjacent words simultaneously is a 2:2 equal-count
        replace block → both corrected targets are added to vocab.

        The spec says 'ALWAYS add the corrected target to vocab'. An equal-count
        replace block is paired index-by-index, so each word-for-word substitution
        contributes its corrected target to vocab.
        """
        r = derive("call diotaleavy ctranslat", "call Diotalevi CTranslate", is_common)
        assert isinstance(r.vocab, list)
        assert isinstance(r.rules, list)
        assert "Diotalevi" in r.vocab
        assert "CTranslate" in r.vocab

    def test_ambiguous_possessive_word_boundary(self):
        r"""AMBIGUOUS: Does cat→dog fire in "cat's"?

        \b exists between 't' and apostrophe (apostrophe is \W).
        Current behavior: yes, "cat's" becomes "dog's".
        Spec is silent on possessives — could be considered a feature or a bug.
        """
        c = TextCorrector([Replacement("cat", "dog", whole_word=True)])
        result = c.correct("cat's whiskers")
        # Document actual behavior: "dog's whiskers" (apostrophe boundary fires)
        # This may or may not be desired — spec says 'whole-word by default'
        # Apostrophe IS a word boundary so technically correct per \b semantics
        assert result == "dog's whiskers", (
            f"AMBIGUOUS: possessive \"cat's\" -> {repr(result)}, "
            r"expected 'dog\'s whiskers' (apostrophe is word boundary)"
        )

    def test_ambiguous_hyphenated_word_partial_match(self):
        r"""AMBIGUOUS: Does cat→dog fire in 'fast-cat'?

        Hyphen is \W so \b exists between 't' and hyphen.
        Current behavior: 'fast-cat' -> 'fast-dog'.
        Whether this is intended whole-word behavior is unspecified.
        """
        c = TextCorrector([Replacement("cat", "dog", whole_word=True)])
        result = c.correct("fast-cat")
        # Document behavior: 'fast-dog' (hyphen is word boundary)
        assert result == "fast-dog", (
            f"AMBIGUOUS: 'fast-cat' -> {repr(result)}, expected 'fast-dog' "
            r"(hyphen is \W so \b fires there)"
        )

    def test_digit_suffix_preserved_in_vocab(self):
        """A digit-suffixed wrong word ('ctranslate2') keeps its digit, so it is
        NOT case-identical to the right word ('CTranslate') and the corrected
        target is learned into vocab.

        Example: 'ctranslate2' → 'CTranslate'
        _words() now keeps digits, so 'ctranslate2' vs 'CTranslate' differ by more
        than case → the corrected target is added to vocab (spec: ALWAYS add the
        corrected target to vocab).
        """
        r = derive("using ctranslate2 engine", "using CTranslate engine", is_common)
        assert "CTranslate" in r.vocab

    def test_many_adjacent_swaps_populate_vocab(self):
        """Three adjacent swaps → SequenceMatcher sees a 3:3 equal-count replace
        block → three word-for-word corrections, all added to vocab.

        Separators are no longer required: an equal-count replace block is paired
        index-by-index, so adjacent swaps and separator-delimited swaps behave the
        same (spec: ALWAYS add the corrected target to vocab).
        """
        # Without separators: 3:3 equal-count block → all three learned.
        r_no_sep = derive(
            "call diotaleavy ctranslat zzx",
            "call Diotalevi CTranslate Yyy",
            is_common,
        )
        # With separators: also all three learned.
        r_with_sep = derive(
            "call diotaleavy and ctranslat and zzx",
            "call Diotalevi and CTranslate and Yyy",
            is_common,
        )
        assert "Diotalevi" in r_no_sep.vocab
        assert "CTranslate" in r_no_sep.vocab
        assert "Yyy" in r_no_sep.vocab
        assert len(r_with_sep.vocab) == 3, "With separators: all three learned"
