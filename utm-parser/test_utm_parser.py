import unittest
from utm_parser import parse_utm

class TestUtmParser(unittest.TestCase):
    def test_full_url(self):
        r = parse_utm("https://x.com/p?utm_source=Google&utm_medium=cpc&utm_campaign=Brand%20Search&utm_term=maid+service&utm_content=ad1&gclid=AbC123")
        self.assertEqual((r.source, r.medium, r.campaign, r.term, r.content),
                         ("google", "cpc", "brand search", "maid service", "ad1"))
        self.assertEqual(r.click_ids["google_click_id"], "AbC123")
        self.assertEqual(r.validate(), [])
    def test_query_string_and_case_keys(self):
        r = parse_utm("?UTM_Source=email&utm_medium=newsletter")
        self.assertEqual(r.source, "email"); self.assertIn("missing required utm_campaign", r.validate())
    def test_dict_input(self):
        self.assertEqual(parse_utm({"utm_campaign": "  Fall  Promo "}).campaign, "fall promo")
    def test_no_utm(self):
        r = parse_utm("https://x.com/"); self.assertFalse(r.has_utm)
    def test_duplicate_first_wins(self):
        self.assertEqual(parse_utm("utm_source=a&utm_source=b").source, "a")
    def test_hash_route(self):
        self.assertEqual(parse_utm("https://x.com/#/book?utm_source=bing").source, "bing")
    def test_unknown_utm_and_truncate(self):
        r = parse_utm("utm_custom=x&utm_campaign=" + "a" * 300)
        self.assertEqual(r.extra, {"utm_custom": "x"}); self.assertEqual(len(r.campaign), 255)
    def test_preserve_case_option(self):
        self.assertEqual(parse_utm("utm_source=Facebook", lowercase=False).source, "Facebook")
    def test_ga4_fields(self):
        r = parse_utm("utm_id=123&utm_source_platform=SA360&utm_creative_format=Video&utm_marketing_tactic=Prospecting")
        self.assertEqual((r.campaign_id, r.source_platform), ("123", "sa360"))
    def test_payload(self):
        p = parse_utm("utm_source=a").to_api_payload(touch="first", location_id="MM-1042")
        self.assertEqual((p["utm_source"], p["touch_type"], p["location_id"]), ("a", "first", "MM-1042"))
    # --- regression tests for review fixes ---
    def test_encoded_plus_preserved(self):
        r = parse_utm("utm_source=c%2B%2B&utm_term=a+b&gclid=Ab%2BCd")
        self.assertEqual((r.source, r.term, r.click_ids["google_click_id"]), ("c++", "a b", "Ab+Cd"))
    def test_duplicates_first_wins_everywhere(self):
        r = parse_utm("gclid=x&gclid=y&utm_x=1&utm_x=2")
        self.assertEqual((r.click_ids, r.extra), ({"google_click_id": "x"}, {"utm_x": "1"}))
    def test_empty_click_id_skipped(self):
        self.assertEqual(parse_utm("gclid=&gclid=b").click_ids, {"google_click_id": "b"})
        self.assertEqual(parse_utm("gclid=").click_ids, {})
    def test_click_id_only_has_attribution(self):
        r = parse_utm("https://x.com/?gclid=Cj0KCQ")
        self.assertFalse(r.has_utm); self.assertTrue(r.has_attribution)
        self.assertFalse(parse_utm("https://x.com/").has_attribution)
    def test_truncate_keeps_whole_characters(self):
        self.assertEqual(parse_utm("utm_campaign=%F0%9F%98%80%F0%9F%98%80", max_length=1).campaign, "\U0001F600")
    def test_truncate_no_trailing_space(self):
        self.assertEqual(parse_utm("utm_campaign=ab%20cd", max_length=3).campaign, "ab")
    def test_payload_clips_urls_and_validates_touch(self):
        p = parse_utm("utm_source=a").to_api_payload(landing_url="h" * 3000, referrer="r" * 2500, touch="bogus")
        self.assertEqual((len(p["landing_url"]), len(p["referrer"]), p["touch_type"]), (2000, 2000, "last"))


if __name__ == "__main__":
    unittest.main()
