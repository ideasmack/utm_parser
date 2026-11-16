"""Tests for the local test server (run: python3 test_server.py)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import server


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.DB_PATH = os.path.join(cls.tmp.name, "test.db")
        server.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.tmp.cleanup()

    def req(self, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_parse(self):
        code, body = self.req("/api/parse", {"input": "utm_source=A&gclid=x"})
        self.assertEqual((code, body["parsed"]["source"]), (200, "a"))

    def test_non_object_json_is_400(self):
        for raw in (b"[1]", b"5", b'"x"', b"null"):
            self.assertEqual(self.req("/api/parse", raw=raw)[0], 400, raw)

    def test_invalid_json_is_400(self):
        self.assertEqual(self.req("/api/parse", raw=b"{nope")[0], 400)

    def test_string_booleans_rejected(self):
        code, body = self.req("/api/parse", {"input": "utm_source=A", "lowercase": "false"})
        self.assertEqual(code, 400); self.assertIn("lowercase", body["error"])

    def test_non_string_input_rejected(self):
        self.assertEqual(self.req("/api/parse", {"input": 123})[0], 400)

    def test_bad_touch_rejected(self):
        code, _ = self.req("/api/capture", {"input": "utm_source=a&utm_medium=b&utm_campaign=c", "touch": "bogus"})
        self.assertEqual(code, 400)

    def test_capture_and_records(self):
        code, body = self.req("/api/capture", {"input": "utm_source=a&utm_medium=b&utm_campaign=c", "touch": "first"})
        self.assertEqual((code, body["saved"]["touch_type"]), (201, "first"))
        code, rows = self.req("/api/records")
        self.assertEqual((code, rows[0]["utm_campaign"]), (200, "c"))

    def test_capture_incomplete_utm_saved_with_warnings(self):
        code, body = self.req("/api/capture", {"input": "utm_source=a"})
        self.assertEqual(code, 201); self.assertIn("missing required utm_campaign", body["warnings"])

    def test_capture_nothing_to_record_is_422(self):
        self.assertEqual(self.req("/api/capture", {"input": "https://x.com/"})[0], 422)

    def test_parse_includes_pos_mapping(self):
        code, body = self.req("/api/parse", {"input": "https://x.com/?gclid=abc"})
        self.assertEqual((code, body["pos"]["lead_source"], body["pos"]["lead_channel"]), (200, "Google", "Paid Search"))
        code, body = self.req("/api/parse", {"input": "https://x.com/", "referrer": "https://www.google.com/"})
        self.assertEqual(body["pos"]["lead_channel"], "Organic Search")

    def test_referrer_must_be_string(self):
        self.assertEqual(self.req("/api/parse", {"input": "x", "referrer": 5})[0], 400)

    def test_unmapped_queue(self):
        for _ in range(2):
            self.req("/api/capture", {"input": "utm_source=zzpartner&utm_medium=blast&utm_campaign=c"})
        code, rows = self.req("/api/unmapped")
        row = next(r for r in rows if r["utm_source"] == "zzpartner")
        self.assertEqual((code, row["count"]), (200, 2))
        code, records = self.req("/api/records")
        self.assertEqual((records[0]["pos_lead_source"], records[0]["mapped"]), ("Unknown", 0))

    def test_mapping_info(self):
        code, body = self.req("/api/mapping")
        self.assertEqual(code, 200); self.assertIn("Google", body["lead_sources"])

    def test_old_database_is_upgraded(self):
        import sqlite3
        old = os.path.join(self.tmp.name, "old.db")
        with sqlite3.connect(old) as db:
            db.execute("CREATE TABLE utm_attribution (id INTEGER PRIMARY KEY, utm_source TEXT)")
        saved, server.DB_PATH = server.DB_PATH, old
        try:
            server.init_db()
            with sqlite3.connect(old) as db:
                cols = {r[1] for r in db.execute("PRAGMA table_info(utm_attribution)")}
            self.assertTrue({"pos_lead_source", "mapped", "rules_version"} <= cols)
        finally:
            server.DB_PATH = saved

    def test_serves_shared_js_parser(self):
        with urllib.request.urlopen(self.base + "/utm-parser/utm_parser.js") as resp:
            self.assertIn(b"UtmParser", resp.read())


if __name__ == "__main__":
    unittest.main()
