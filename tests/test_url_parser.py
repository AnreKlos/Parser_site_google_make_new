"""Tests for utils/url_parser.py — own site vs external marketplace classification."""

from utils.url_parser import classify_url, is_social_url


# ======================================================================
# Avito — external marketplace, never own site
# ======================================================================

class TestAvitoNotOwnSite:
    def test_avito_company_page(self):
        assert classify_url("https://www.avito.ru/bryansk/predlozheniya_uslug") == ("social", "avito")

    def test_avito_subdomain(self):
        assert classify_url("https://m.avito.ru/123456") == ("social", "avito")

    def test_avito_item_url(self):
        url_type, _ = classify_url("https://www.avito.ru/moskva/uslugi/stroitelstvo_123")
        assert url_type != "website"


# ======================================================================
# Maps and catalogs — external, never own site
# ======================================================================

class TestMapsNotOwnSite:
    def test_yandex_maps_org(self):
        assert classify_url("https://yandex.ru/maps/org/mood/96195832006/") == ("social", "yandex_maps")

    def test_maps_yandex_subdomain(self):
        assert classify_url("https://maps.yandex.ru/?text=салон") == ("social", "yandex_maps")

    def test_2gis(self):
        assert classify_url("https://2gis.ru/bryansk/firm/123") == ("social", "2gis")

    def test_zoon(self):
        assert classify_url("https://zoon.ru/msk/beauty/salon_123/") == ("social", "zoon")

    def test_yclients(self):
        assert classify_url("https://yclients.com/company/123") == ("social", "yclients")


# ======================================================================
# Socials — external, never own site
# ======================================================================

class TestSocialsNotOwnSite:
    def test_vk(self):
        assert classify_url("https://vk.com/salon32") == ("social", "vk")

    def test_telegram(self):
        assert classify_url("https://t.me/salon32") == ("social", "telegram")

    def test_instagram(self):
        assert classify_url("https://instagram.com/salon32") == ("social", "instagram")

    def test_facebook(self):
        assert classify_url("https://facebook.com/salon32") == ("social", "facebook")

    def test_youtube(self):
        assert classify_url("https://youtube.com/@salon32") == ("social", "youtube")


# ======================================================================
# Own company domain — website
# ======================================================================

class TestOwnSite:
    def test_company_ru_domain(self):
        assert classify_url("https://salon32.ru") == ("website", None)

    def test_company_ru_with_path(self):
        assert classify_url("https://kottedj-servis.ru/uslugi") == ("website", None)

    def test_company_http(self):
        assert classify_url("http://palchiki.com/") == ("website", None)

    def test_is_social_url_false_for_own_site(self):
        assert is_social_url("https://salon32.ru") is False

    def test_is_social_url_true_for_avito(self):
        assert is_social_url("https://www.avito.ru/bryansk/123") is True
