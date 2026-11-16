import json
import os
import tempfile
import time
import unittest

import utm_mapping
from utm_mapping import Mapper, MappingConfigError, load_mapper, payload_from_form
from utm_parser import parse_utm

CONFIG = {
    "lead_sources": ["Google", "Facebook", "Partner", "Website", "Referral", "Unknown"],
    "lead_channels": ["Paid Search", "Paid Social", "Organic Search", "Partner", "Direct", "Referral", "Unmapped"],
    "fallback": {"lead_source": "Unknown", "lead_channel": "Unmapped"},
    "pos_fields_touch": "first",
    "internal_domains": ["mysite.com"],
    "note_max_length": 400,
}
HEADER = "rule_id,priority,location_id,has_utm,source,medium,campaign,click_id,referrer,lead_source,lead_channel,notes\n"
RULES = HEADER + """# comment row,,,,,,,,,,,
partner,5,,yes,agency*,,,,,Partner,Partner,
partner_local,4,LOC-1,yes,agency*,,,,,Facebook,Paid Social,location override
google_paid,10,,yes,google,cpc|paid*,,,,Google,Paid Search,
fb_paid,20,,yes,facebook|fb,paid*|(none),,,,Facebook,Paid Social,
gclid,80,,no,,,,google_*,,Google,Paid Search,
ref_google,85,,no,,,,,google.*|*.google.*,Google,Organic Search,
ref_any,95,,no,,,,,*,Referral,Referral,
direct,99,,no,,,,(none),(none),Website,Direct,
"""


def touch(url, **ctx):
    return parse_utm(url).to_api_payload(**ctx)


class TestMapping(unittest.TestCase):
    def setUp(self):
        self.m = Mapper.from_text(RULES, CONFIG)

    def check(self, payload, source, channel, rule):
        r = self.m.map(payload)
        self.assertEqual((r.lead_source, r.lead_channel, r.rule_id), (source, channel, rule))

    def test_aliases_wildcards_and_case(self):
        self.check(touch("utm_source=Google&utm_medium=CPC"), "Google", "Paid Search", "google_paid")
        self.check(touch("utm_source=FB&utm_medium=paid_social"), "Facebook", "Paid Social", "fb_paid")
        self.check(touch("utm_source=Google&utm_medium=paidsearch", ), "Google", "Paid Search", "google_paid")
        self.check(parse_utm("utm_source=GOOGLE&utm_medium=CPC", lowercase=False).to_api_payload(), "Google", "Paid Search", "google_paid")

    def test_none_token_matches_empty(self):
        self.check(touch("utm_source=facebook"), "Facebook", "Paid Social", "fb_paid")

    def test_priority_and_location_scope(self):
        self.check(touch("utm_source=agency_x&utm_medium=email"), "Partner", "Partner", "partner")
        self.check(touch("utm_source=agency_x", location_id="LOC-1"), "Facebook", "Paid Social", "partner_local")

    def test_unknown_values_fall_back_unmapped(self):
        r = self.m.map(touch("utm_source=superleads&utm_medium=blast"))
        self.assertEqual((r.lead_source, r.lead_channel, r.mapped, r.rule_id), ("Unknown", "Unmapped", False, None))

    def test_no_utm_fallbacks(self):
        self.check(touch("https://x.com/?gclid=abc"), "Google", "Paid Search", "gclid")
        self.check(touch("https://x.com/", referrer="https://www.google.co.uk/search"), "Google", "Organic Search", "ref_google")
        self.check(touch("https://x.com/", referrer="https://blog.example.org/post"), "Referral", "Referral", "ref_any")
        self.check(touch("https://x.com/"), "Website", "Direct", "direct")

    def test_internal_referrer_is_direct(self):
        self.check(touch("https://x.com/", referrer="https://www.mysite.com/a"), "Website", "Direct", "direct")
        self.check(touch("https://x.com/", referrer="https://book.mysite.com/a"), "Website", "Direct", "direct")

    def test_rules_version_changes_with_rules(self):
        other = Mapper.from_text(RULES.replace("cpc|paid*", "cpc"), CONFIG)
        self.assertNotEqual(self.m.version, other.version)
        self.assertEqual(self.m.map(touch("utm_source=google&utm_medium=cpc")).rules_version, self.m.version)


class TestValidation(unittest.TestCase):
    def errors(self, rules=RULES, config=CONFIG):
        with self.assertRaises(MappingConfigError) as cm:
            Mapper.from_text(rules, config)
        return str(cm.exception)

    def test_output_not_in_pos_lists(self):
        self.assertIn("lead_source 'Gogle' is not in", self.errors(RULES + "typo,50,,yes,x,,,,,Gogle,Paid Search,\n"))
        self.assertIn("lead_channel 'Paid search' is not in", self.errors(RULES + "case,50,,yes,x,,,,,Google,Paid search,\n"))

    def test_all_errors_reported_together(self):
        e = self.errors(RULES + "partner,x,,maybe,,,,,,Nope,Nope,\n")
        for msg in ("duplicate rule_id", "priority must be", "has_utm must be", "lead_source 'Nope'", "lead_channel 'Nope'", "no conditions"):
            self.assertIn(msg, e)

    def test_bad_headers(self):
        self.assertIn("missing column 'priority'", self.errors("rule_id,lead_source,lead_channel,source\n"))
        self.assertIn("unknown column 'sorce'", self.errors(HEADER.replace("source,", "sorce,", 1)))

    def test_bad_config(self):
        self.assertIn("fallback lead_source", self.errors(config=dict(CONFIG, fallback={"lead_source": "X", "lead_channel": "Unmapped"})))
        self.assertIn("pos_fields_touch", self.errors(config=dict(CONFIG, pos_fields_touch="middle")))
        self.assertIn("'lead_sources'", self.errors(config=dict(CONFIG, lead_sources=[])))

    def test_shipped_files_are_valid(self):
        m = Mapper.from_files()
        self.assertGreater(len(m.rules), 10)
        self.assertEqual(m.map(touch("https://x.com/?utm_source=Facebook&utm_medium=paid_social")).lead_source, "Facebook")


class TestPosRecord(unittest.TestCase):
    def setUp(self):
        self.m = Mapper.from_text(RULES, CONFIG)

    def test_uses_configured_touch_and_falls_back(self):
        first = touch("https://x.com/?gclid=abc", touch="first")
        last = touch("utm_source=fb&utm_medium=paid", touch="last")
        rec = self.m.build_pos_record(last_touch=last, first_touch=first)
        self.assertEqual((rec["lead_source"], rec["mapping"]["touch"]), ("Google", "first"))
        rec = self.m.build_pos_record(last_touch=last)
        self.assertEqual((rec["lead_source"], rec["mapping"]["touch"]), ("Facebook", "last"))
        rec = Mapper.from_text(RULES, dict(CONFIG, pos_fields_touch="last")).build_pos_record(last, first)
        self.assertEqual(rec["lead_source"], "Facebook")

    def test_no_touches_gives_fallback(self):
        rec = self.m.build_pos_record(None, None)
        self.assertEqual((rec["lead_source"], rec["mapping"]["mapped"]), ("Unknown", False))

    def test_note_is_readable_and_has_no_query_string(self):
        p = touch("https://www.mysite.com/book?utm_source=google&utm_medium=cpc&utm_campaign=brand&email=a@b.com&gclid=x",
                  landing_url="https://www.mysite.com/book?utm_source=google&email=a@b.com")
        note = self.m.build_pos_record(p, p)["note"]
        self.assertIn("Lead: Google / Paid Search (first touch, rule google_paid)", note)
        self.assertIn("source=google · medium=cpc · campaign=brand", note)
        self.assertIn("landing: www.mysite.com/book", note)
        self.assertIn("click IDs: google_click_id", note)
        self.assertNotIn("a@b.com", note)

    def test_unmapped_note_and_length_limit(self):
        p = touch("utm_source=zz&utm_campaign=" + "c" * 250 + "&utm_term=" + "t" * 250)
        note = self.m.build_pos_record(p, p)["note"]
        self.assertIn("UNMAPPED", note)
        self.assertLessEqual(len(note), 400); self.assertTrue(note.endswith("…"))


class TestFormAndReload(unittest.TestCase):
    def test_payload_from_form(self):
        form = {"first_utm_source": " Google ", "first_utm_medium": "CPC", "first_click_ids": '{"google_click_id":"Ab+1","evil":"x"}',
                "first_landing_url": "https://x.com/a", "first_referrer": "https://www.google.com/", "location_id": "LOC-9",
                "last_utm_source": "", "last_click_ids": "not json"}
        p = payload_from_form(form, "first")
        self.assertEqual((p["utm_source"], p["utm_medium"], p["touch_type"], p["location_id"]), ("google", "cpc", "first", "LOC-9"))
        self.assertEqual(p["click_ids"], {"google_click_id": "Ab+1"})
        self.assertEqual(p["referrer"], "https://www.google.com/")
        self.assertIsNone(payload_from_form(form, "last"))
        with self.assertRaises(ValueError):
            payload_from_form(form, "middle")

    def test_load_mapper_reloads_on_change_and_rejects_bad_edits(self):
        with tempfile.TemporaryDirectory() as d:
            rules, cfg = os.path.join(d, "r.csv"), os.path.join(d, "c.json")
            with open(cfg, "w") as f:
                json.dump(CONFIG, f)
            with open(rules, "w") as f:
                f.write(RULES)
            v1 = load_mapper(rules, cfg).version
            self.assertIs(load_mapper(rules, cfg), load_mapper(rules, cfg))
            time.sleep(0.01)
            with open(rules, "a") as f:
                f.write("new,50,,yes,x,,,,,Google,Paid Search,\n")
            os.utime(rules, (time.time() + 5, time.time() + 5))
            self.assertNotEqual(load_mapper(rules, cfg).version, v1)
            with open(rules, "a") as f:
                f.write("bad,50,,yes,y,,,,,Nope,Paid Search,\n")
            os.utime(rules, (time.time() + 10, time.time() + 10))
            with self.assertRaises(MappingConfigError):
                load_mapper(rules, cfg)
        utm_mapping._cache.clear()


if __name__ == "__main__":
    unittest.main()
