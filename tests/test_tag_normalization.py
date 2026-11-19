"""Unit tests for the pure tag normalization functions."""

import pytest

from app.services.tag_normalization import (
    MAX_TAG_LENGTH,
    MAX_TAGS_PER_NOTE,
    normalize_tag,
    normalize_tags,
)

DECOMPOSED_I = "İ"  # 'I' + COMBINING DOT ABOVE, i.e. a decomposed 'İ'


class TestNormalizeTag:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("İş", "iş"),
            ("ISPARTA", "ısparta"),
            ("Öğrenim", "öğrenim"),
            ("ÇALIŞMA", "çalışma"),
            ("ılık", "ılık"),
            ("Kitap", "kitap"),
        ],
    )
    def test_turkish_aware_lowercase(self, raw: str, expected: str) -> None:
        assert normalize_tag(raw) == expected

    def test_dotted_capital_does_not_leave_combining_dot(self) -> None:
        # Plain str.lower() would give 'i̇ş' (3 code points).
        result = normalize_tag("İş")
        assert result == "iş"
        assert "̇" not in result
        assert len(result) == 2

    def test_decomposed_dotted_capital_matches_precomposed(self) -> None:
        decomposed = normalize_tag(DECOMPOSED_I + "ş")
        assert decomposed == normalize_tag("İş") == "iş"
        assert "̇" not in decomposed
        assert "ı̇" not in decomposed

    def test_trims_and_collapses_whitespace(self) -> None:
        assert normalize_tag("  Proje   Fikirleri \t ") == "proje fikirleri"

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
    def test_empty_or_whitespace_only_raises(self, raw: str) -> None:
        with pytest.raises(ValueError, match="empty"):
            normalize_tag(raw)

    def test_comma_raises(self) -> None:
        with pytest.raises(ValueError, match="comma"):
            normalize_tag("iş,ev")

    def test_length_limit_is_40_characters(self) -> None:
        assert normalize_tag("a" * MAX_TAG_LENGTH) == "a" * 40
        with pytest.raises(ValueError, match="longer"):
            normalize_tag("a" * (MAX_TAG_LENGTH + 1))

    def test_length_counted_after_whitespace_collapse(self) -> None:
        assert normalize_tag("  " + "a" * 40 + "  ") == "a" * 40

    def test_length_counts_unicode_characters_not_bytes(self) -> None:
        assert normalize_tag("ş" * 40) == "ş" * 40
        assert normalize_tag("🎯" * 40) == "🎯" * 40
        with pytest.raises(ValueError, match="longer"):
            normalize_tag("🎯" * 41)


class TestNormalizeTags:
    def test_dedupes_after_normalization_keeping_first_order(self) -> None:
        assert normalize_tags(["İş", "ev", "iş", " İŞ ", DECOMPOSED_I + "ş"]) == ["iş", "ev"]

    def test_empty_list(self) -> None:
        assert normalize_tags([]) == []

    def test_limit_is_20_unique_tags(self) -> None:
        twenty = [f"t{i}" for i in range(MAX_TAGS_PER_NOTE)]
        assert normalize_tags(twenty) == twenty
        with pytest.raises(ValueError, match="more than 20"):
            normalize_tags([*twenty, "t20"])

    def test_limit_applies_after_dedupe(self) -> None:
        twenty = [f"t{i}" for i in range(MAX_TAGS_PER_NOTE)]
        assert len(normalize_tags([*twenty, "T0"])) == 20

    def test_invalid_entry_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_tags(["iş", " "])
