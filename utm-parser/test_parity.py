"""Checks that utm_parser.py and utm_parser.js produce identical results (needs Node.js)."""
import json
import os
import shutil
import subprocess
import unittest

from utm_parser import parse_utm

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = [
    "https://x.com/p?utm_source=Google&utm_medium=cpc&utm_campaign=Brand%20Search&utm_term=maid+service&gclid=AbC123",
    "?UTM_Source=email&utm_medium=newsletter",
    "https://x.com/#/book?utm_source=%20%20Bing%20&utm_source=dup&utm_medium=CPC&utm_campaign=Spring++Sale&utm_custom=abc",
    "https://x.com/?utm_source=a#/p?utm_source=b&utm_medium=hash",
    "gclid=a&gclid=b&utm_x=1&utm_x=2&fbclid=&msclkid=M1",
    "utm_source=c%2B%2B&gclid=Ab%2BCd",
    "utm_campaign=%F0%9F%98%80%F0%9F%98%80&utm_term=" + "x" * 300,
    "utm_id=123&utm_source_platform=SA360&utm_creative_format=Video&utm_marketing_tactic=Prospecting",
    "utm_source=%00tab%09here%7F&utm_medium=%E2%80%83wide%E2%80%83",
    "https://x.com/services/",
    "",
]
OPTIONS = [(True, True), (False, True), (True, False)]
FIELDS = ["source", "medium", "campaign", "term", "content", "campaign_id",
          "source_platform", "creative_format", "marketing_tactic"]

JS = """
const U = require(process.argv[1]);
const {cases, options} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = [];
for (const c of cases) for (const [lowercase, includeClickIds] of options) {
  const r = U.parse(c, {lowercase, includeClickIds});
  out.push({r, errors: U.validate(r).errors, attribution: U.hasAttribution(r)});
}
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class TestParity(unittest.TestCase):
    def test_python_matches_js(self):
        proc = subprocess.run(["node", "-e", JS, os.path.join(HERE, "utm_parser.js")],
                              input=json.dumps({"cases": CASES, "options": OPTIONS}),
                              capture_output=True, text=True, check=True)
        js_results = iter(json.loads(proc.stdout))
        for case in CASES:
            for lowercase, click in OPTIONS:
                py = parse_utm(case, lowercase=lowercase, include_click_ids=click)
                js = next(js_results)
                expected = {f: getattr(py, f) for f in FIELDS}
                expected.update(click_ids=py.click_ids, extra=py.extra, has_utm=py.has_utm)
                with self.subTest(case=case, lowercase=lowercase, click_ids=click):
                    self.assertEqual(js["r"], expected)
                    self.assertEqual(js["errors"], py.validate())
                    self.assertEqual(js["attribution"], py.has_attribution)


if __name__ == "__main__":
    unittest.main()
