"""
Unit tests for language-disambiguation helpers in search_service.

No DB, no embeddings — all pure-Python logic.
"""
import pytest
from api.services.search_service import (
    _extract_lang_keywords,
    _extract_topic_words,
    _language_tier,
    _topic_tier,
    _keyword_bonus,
    _LANG_COMPETITORS,
)


# ── _extract_lang_keywords ────────────────────────────────────────────────────

class TestExtractLangKeywords:
    def test_detects_java(self):
        assert "java" in _extract_lang_keywords("interface in java")

    def test_java_does_not_match_javascript(self):
        # "javascript" as a standalone word must NOT trigger the "java" rule
        langs = _extract_lang_keywords("interface in javascript")
        assert "java" not in langs
        assert "javascript" in langs

    def test_detects_python(self):
        assert "python" in _extract_lang_keywords("how to use decorators in python")

    def test_detects_typescript(self):
        assert "typescript" in _extract_lang_keywords("TypeScript generics tutorial")

    def test_no_language_keyword(self):
        assert _extract_lang_keywords("how to sort a list") == []

    def test_multiple_languages(self):
        langs = _extract_lang_keywords("difference between java and kotlin")
        assert "java" in langs
        assert "kotlin" in langs

    def test_case_insensitive(self):
        # query words are lowercased before matching
        assert "python" in _extract_lang_keywords("Python decorators")


# ── _language_tier ────────────────────────────────────────────────────────────

class TestLanguageTier:
    # ── Java vs JavaScript ────────────────────────────────────────────────────

    def test_java_chunk_gets_tier_2(self):
        tier = _language_tier(
            ["java"],
            "In Java, an interface is a reference type that can contain method signatures.",
            "Java OOP Tutorial",
        )
        assert tier == 2

    def test_javascript_chunk_penalised_for_java_query(self):
        tier = _language_tier(
            ["java"],
            "In JavaScript you can define interfaces using TypeScript syntax.",
            "JavaScript Tutorial",
        )
        assert tier == -1

    def test_typescript_chunk_penalised_for_java_query(self):
        tier = _language_tier(
            ["java"],
            "TypeScript interfaces allow structural typing.",
            "TypeScript Deep Dive",
        )
        assert tier == -1

    def test_java_word_inside_javascript_text_is_not_a_java_hit(self):
        # The word "java" appears inside "javascript" — must NOT count as a Java hit
        tier = _language_tier(
            ["java"],
            "javascript is a popular language for the web",
            "JavaScript Course",
        )
        # "java" doesn't appear as a whole word → no query match
        # "javascript" IS a competitor → tier should be -1
        assert tier == -1

    def test_chunk_with_both_java_and_javascript_is_ambiguous(self):
        tier = _language_tier(
            ["java"],
            "Both Java and JavaScript share the name but are very different languages.",
            "Java vs JavaScript",
        )
        assert tier == 1   # has query lang AND competitor → ambiguous

    # ── Neutral / unknown language ────────────────────────────────────────────

    def test_neutral_chunk_gets_tier_0(self):
        tier = _language_tier(
            ["java"],
            "This tutorial explains how interfaces work in object-oriented programming.",
            "OOP concepts",
        )
        assert tier == 0

    def test_no_query_langs_always_tier_0(self):
        tier = _language_tier([], "Some random text about sorting algorithms.", "")
        assert tier == 0

    # ── Title-based matching ──────────────────────────────────────────────────

    def test_java_in_title_boosts_tier(self):
        tier = _language_tier(
            ["java"],
            "Interfaces allow you to define a contract.",   # no language in text
            "Java Interfaces Explained",                    # but title says java
        )
        assert tier == 2

    def test_competitor_in_title_penalises(self):
        tier = _language_tier(
            ["java"],
            "Interfaces are a core concept in typed languages.",
            "TypeScript Handbook",
        )
        assert tier == -1

    # ── Python ───────────────────────────────────────────────────────────────

    def test_python_chunk_is_correct(self):
        tier = _language_tier(
            ["python"],
            "In Python, decorators are a powerful feature using the @ syntax.",
            "Python Tips",
        )
        assert tier == 2

    def test_ruby_chunk_penalised_for_python_query(self):
        tier = _language_tier(
            ["python"],
            "Ruby uses blocks and procs instead of decorators.",
            "Ruby Programming",
        )
        assert tier == -1

    # ── TypeScript vs JavaScript ──────────────────────────────────────────────

    def test_typescript_chunk_correct_for_ts_query(self):
        tier = _language_tier(
            ["typescript"],
            "TypeScript generics let you write reusable, type-safe code.",
            "TypeScript Advanced",
        )
        assert tier == 2

    def test_javascript_chunk_penalised_for_ts_query(self):
        tier = _language_tier(
            ["typescript"],
            "JavaScript doesn't have built-in type checking.",
            "JavaScript Basics",
        )
        assert tier == -1


# ── Sort ordering ─────────────────────────────────────────────────────────────

class TestLanguageSortOrder:
    """Verify that tier + similarity together produce the right ranking."""

    def _make_result(self, text: str, title: str, similarity: float):
        return {"text": text, "title": title, "similarity": similarity}

    def test_java_results_rank_above_js_for_java_query(self):
        q_langs = _extract_lang_keywords("interface in java")
        results = [
            self._make_result(
                "JavaScript uses prototype-based interfaces.",
                "JavaScript Tutorial", 0.82),
            self._make_result(
                "In Java, interface is a keyword used to declare an interface.",
                "Java OOP", 0.75),
            self._make_result(
                "TypeScript adds static typing to JavaScript via interfaces.",
                "TypeScript Guide", 0.80),
        ]
        results.sort(
            key=lambda r: (_language_tier(q_langs, r["text"], r["title"]), r["similarity"]),
            reverse=True,
        )
        assert results[0]["title"] == "Java OOP"

    def test_higher_similarity_wins_when_same_tier(self):
        q_langs = _extract_lang_keywords("interface in java")
        results = [
            self._make_result("Java interface concept explained.", "Java Basics", 0.70),
            self._make_result("Java interfaces and abstract classes.", "Java Advanced", 0.85),
        ]
        results.sort(
            key=lambda r: (_language_tier(q_langs, r["text"], r["title"]), r["similarity"]),
            reverse=True,
        )
        assert results[0]["similarity"] == 0.85

    def test_competitor_always_loses_to_neutral(self):
        q_langs = _extract_lang_keywords("interface in java")
        results = [
            self._make_result("TypeScript interfaces explained.", "TypeScript", 0.90),
            self._make_result("What is an interface in programming?", "OOP Concepts", 0.65),
        ]
        results.sort(
            key=lambda r: (_language_tier(q_langs, r["text"], r["title"]), r["similarity"]),
            reverse=True,
        )
        # Neutral (tier 0, sim 0.65) should beat competitor (tier -1, sim 0.90)
        assert results[0]["title"] == "OOP Concepts"


# ── _extract_topic_words ─────────────────────────────────────────────────────

class TestExtractTopicWords:
    def test_returns_interface_from_python_query(self):
        langs = _extract_lang_keywords("interface in python")
        words = _extract_topic_words("interface in python", langs)
        assert "interface" in words

    def test_strips_language_keyword(self):
        langs = _extract_lang_keywords("interface in python")
        words = _extract_topic_words("interface in python", langs)
        assert "python" not in words

    def test_strips_stopwords(self):
        langs = _extract_lang_keywords("how to use decorators in python")
        words = _extract_topic_words("how to use decorators in python", langs)
        assert "how" not in words
        assert "to" not in words
        assert "in" not in words
        assert "decorators" in words

    def test_strips_short_words(self):
        langs = []
        words = _extract_topic_words("OOP in go", langs)
        assert "go" not in words   # len < 3

    def test_empty_query(self):
        assert _extract_topic_words("", []) == []

    def test_no_meaningful_words(self):
        # All stopwords
        assert _extract_topic_words("how to do it", []) == []


# ── _topic_tier ───────────────────────────────────────────────────────────────

class TestTopicTier:
    def test_returns_1_when_topic_word_in_text(self):
        assert _topic_tier(["interface"], "In Python, you use ABC to define an interface.") == 1

    def test_returns_0_when_topic_word_absent(self):
        assert _topic_tier(["interface"], "Python has int, float, str, and list datatypes.") == 0

    def test_empty_topic_words_returns_0(self):
        assert _topic_tier([], "any text here") == 0

    def test_case_insensitive_match(self):
        assert _topic_tier(["interface"], "Interface Segregation Principle in Python") == 1

    def test_partial_word_match_allowed(self):
        # "interface" found inside "interfaces"
        assert _topic_tier(["interface"], "Python interfaces using Protocol class") == 1


# ── Combined sort: language tier + semantic score ─────────────────────────────

class TestCombinedSort:
    """
    Verifies step 7c: cross-encoder DESC order is preserved; wrong-language
    results (tier == -1) are moved to the end without touching the rest.
    """

    def _make(self, text: str, title: str, sim: float, cross: float | None = None) -> dict:
        return {"text": text, "title": title, "similarity": sim, "cross_score": cross}

    def _sort(self, query: str, results: list[dict]) -> list[dict]:
        """Mirrors production step 7c exactly."""
        q_langs = _extract_lang_keywords(query)
        good    = [r for r in results if _language_tier(q_langs, r["text"], r["title"]) >= 0]
        demoted = [r for r in results if _language_tier(q_langs, r["text"], r["title"]) <  0]
        return good + demoted

    def test_cross_encoder_desc_order_preserved(self):
        # Input already sorted DESC by cross_score — output must keep that order.
        results = self._sort("interface in python", [
            self._make("Python ABC defines abstract interfaces.", "Python ABC",
                       sim=0.72, cross=0.91),
            self._make("Python datatypes int float str.", "Python Datatypes",
                       sim=0.80, cross=0.42),
        ])
        assert results[0]["title"] == "Python ABC"
        assert results[1]["title"] == "Python Datatypes"

    def test_wrong_language_moved_to_end(self):
        results = self._sort("interface in java", [
            self._make("JavaScript prototype patterns.", "JS Tutorial",
                       sim=0.85, cross=0.95),   # tier -1 → end
            self._make("Java interface keyword.", "Java OOP",
                       sim=0.71, cross=0.80),
            self._make("OOP design contracts.", "OOP Concepts",
                       sim=0.65, cross=0.70),
        ])
        assert results[0]["title"] == "Java OOP"
        assert results[1]["title"] == "OOP Concepts"
        assert results[2]["title"] == "JS Tutorial"

    def test_ruby_demoted_to_end_for_python_query(self):
        results = self._sort("interface in python", [
            self._make("Python ABC abstract interfaces.", "Python ABC",
                       sim=0.72, cross=0.91),
            self._make("Ruby modules as mixins.", "Ruby Guide",
                       sim=0.88, cross=0.80),   # tier -1
        ])
        assert results[0]["title"] == "Python ABC"
        assert results[-1]["title"] == "Ruby Guide"

    def test_good_results_keep_desc_order_with_multiple_demoted(self):
        results = self._sort("interface in python", [
            self._make("OOP design patterns.", "OOP A", sim=0.80, cross=0.88),
            self._make("Abstract class concepts.", "OOP B", sim=0.75, cross=0.72),
            self._make("Ruby mixins.", "Ruby", sim=0.90, cross=0.95),   # tier -1
            self._make("Perl OOP.", "Perl", sim=0.70, cross=0.60),     # tier -1
        ])
        assert results[0]["title"] == "OOP A"
        assert results[1]["title"] == "OOP B"
        assert set(r["title"] for r in results[2:]) == {"Ruby", "Perl"}

    def test_bi_encoder_path_wrong_lang_demoted(self):
        results = self._sort("interface in java", [
            self._make("JavaScript inheritance.", "JS Tutorial", sim=0.85, cross=None),
            self._make("Java interface keyword.", "Java OOP",   sim=0.71, cross=None),
        ])
        assert results[0]["title"] == "Java OOP"
        assert results[-1]["title"] == "JS Tutorial"

    def test_no_lang_keyword_order_unchanged(self):
        results = self._sort("what is an interface", [
            self._make("OOP design contracts.", "OOP Guide", sim=0.80, cross=0.90),
            self._make("Java interface keyword.", "Java OOP",  sim=0.75, cross=0.85),
        ])
        assert results[0]["title"] == "OOP Guide"
        assert results[1]["title"] == "Java OOP"


# ── _keyword_bonus ────────────────────────────────────────────────────────────

class TestKeywordBonus:
    def test_bonus_when_keyword_matches(self):
        bonus = _keyword_bonus("java interface", "In Java, interfaces define a contract.")
        assert bonus > 0.0

    def test_no_bonus_when_no_tech_keyword_in_query(self):
        assert _keyword_bonus("how to sort a list", "sorting algorithms in python") == 0.0

    def test_bonus_capped_at_008(self):
        # Many keyword matches should not exceed the cap
        query = "java python javascript typescript golang rust"
        text  = "java python javascript typescript golang rust tutorial"
        assert _keyword_bonus(query, text) <= 0.08

    def test_bonus_zero_when_keyword_not_in_text(self):
        assert _keyword_bonus("java interface", "C++ templates are powerful.") == 0.0


# ── _LANG_COMPETITORS sanity ──────────────────────────────────────────────────

class TestLangCompetitorsTable:
    def test_java_competitors_include_javascript(self):
        assert "javascript" in _LANG_COMPETITORS["java"]

    def test_java_competitors_include_typescript(self):
        assert "typescript" in _LANG_COMPETITORS["java"]

    def test_javascript_competitor_is_typescript(self):
        assert "typescript" in _LANG_COMPETITORS["javascript"]

    def test_python_competitors_do_not_include_java(self):
        assert "java" not in _LANG_COMPETITORS.get("python", frozenset())
