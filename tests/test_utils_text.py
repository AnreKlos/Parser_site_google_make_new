"""Tests for utils/text.py — pure text-processing functions."""

from utils.text import (
    compact_text,
    slugify_name,
    detect_city,
    fallback_author_name,
)


# ======================================================================
# compact_text
# ======================================================================

class TestCompactText:
    def test_normal_text_unchanged(self):
        assert compact_text("Салон красоты") == "Салон красоты"

    def test_multiple_spaces_collapsed(self):
        assert compact_text("Салон   красоты") == "Салон красоты"

    def test_leading_trailing_spaces_trimmed(self):
        assert compact_text("  Салон красоты  ") == "Салон красоты"

    def test_tabs_and_newlines_replaced(self):
        assert compact_text("Салон\tкрасоты\nоткрыт") == "Салон красоты открыт"

    def test_empty_string(self):
        assert compact_text("") == ""

    def test_only_whitespace(self):
        assert compact_text("   \t  \n  ") == ""

    def test_mixed_cyrillic_latin(self):
        assert compact_text("Hello   мир") == "Hello мир"


# ======================================================================
# slugify_name
# ======================================================================

class TestSlugifyName:
    def test_cyrillic_transliterated(self):
        assert slugify_name("Салон красоты", 1) == "salon-krasoty"

    def test_single_word(self):
        assert slugify_name("Маникюр", 2) == "manikyur"

    def test_mixed_case_lowered(self):
        assert slugify_name("САЛОН Красоты", 3) == "salon-krasoty"

    def test_empty_name_falls_back_to_lead_id(self):
        assert slugify_name("", 5) == "lead-5"

    def test_whitespace_only_falls_back(self):
        assert slugify_name("   ", 7) == "lead-7"

    def test_special_characters_stripped(self):
        assert slugify_name("Студия#1★", 9) == "studiya-1"

    def test_latin_text_preserved(self):
        assert slugify_name("Beauty Studio", 11) == "beauty-studio"

    def test_mixed_cyrillic_latin(self):
        assert slugify_name("Beauty студия", 13) == "beauty-studiya"

    def test_apostrophe_and_hyphen_handling(self):
        assert slugify_name("Жан-Поль салон", 15) == "zhan-pol-salon"

    def test_yo_letter_transliterated(self):
        assert slugify_name("Ёлка beauty", 17) == "elka-beauty"

    def test_already_sluglike_stable(self):
        assert slugify_name("salon-krasoty", 19) == "salon-krasoty"

    def test_multiple_hyphens_collapsed(self):
        assert slugify_name("Салон   красоты!!", 21) == "salon-krasoty"

    def test_leading_trailing_hyphens_stripped(self):
        assert slugify_name("!!Салон красоты!!", 23) == "salon-krasoty"

    def test_numbers_preserved(self):
        assert slugify_name("Студия 54", 25) == "studiya-54"


# ======================================================================
# detect_city
# ======================================================================

class TestDetectCity:
    def test_full_address_with_g_prefix(self):
        assert detect_city("г. Москва, ул. Тверская, д. 1") == "Москва"

    def test_city_without_prefix(self):
        assert detect_city("Москва, ул. Тверская") == "Москва"

    def test_multi_word_city(self):
        assert detect_city("Нижний Новгород, ул. Ленина") == "Нижний Новгород"

    def test_street_first_city_last(self):
        assert detect_city("ул. Ленина, д. 1, Москва") == "Москва"

    def test_country_only_skipped_street_also_skipped(self):
        assert detect_city("Россия, ул. Тверская") == "вашем городе"

    def test_region_skipped_city_extracted(self):
        assert detect_city("Московская обл, г. Серпухов") == "Серпухов"

    def test_empty_string(self):
        assert detect_city("") == "вашем городе"

    def test_none_input(self):
        assert detect_city(None) == "вашем городе"

    def test_city_without_space_after_dot(self):
        assert detect_city("г.Казань, ул. Баумана") == "Казань"

    def test_country_only_no_city(self):
        assert detect_city("Россия") == "вашем городе"

    def test_house_number_first(self):
        assert detect_city("д. 10, Москва") == "Москва"

    def test_boulevard_marker(self):
        assert detect_city("Москва, б-р Мира") == "Москва"

    def test_avenue_marker(self):
        assert detect_city("г Санкт-Петербург, пр-т Невский") == "Санкт-Петербург"

    def test_district_skipped(self):
        assert detect_city("Краснодарский край, г. Сочи") == "Сочи"


# ======================================================================
# fallback_author_name
# ======================================================================

class TestFallbackAuthorName:
    def test_full_name_returns_first_name(self):
        assert fallback_author_name("Иван Иванов", 0) == "Иван"

    def test_empty_string_fallback_index_0(self):
        assert fallback_author_name("", 0) == "Мария"

    def test_none_fallback_index_1(self):
        assert fallback_author_name(None, 1) == "Анна"

    def test_cyrillic_only_name(self):
        assert fallback_author_name("Елена", 2) == "Елена"

    def test_latin_no_cyrillic_fallback(self):
        assert fallback_author_name("John Doe", 3) == "Елена"

    def test_special_chars_cleaned(self):
        assert fallback_author_name("@название", 4) == "Название"

    def test_index_wraps_around(self):
        assert fallback_author_name("", 6) == "Мария"

    def test_full_name_with_patronymic(self):
        assert fallback_author_name("Иван Петрович Сидоров", 0) == "Иван"

    def test_whitespace_only_fallback(self):
        assert fallback_author_name("  ", 1) == "Анна"
