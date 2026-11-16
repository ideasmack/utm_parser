"""
Local UTM test server (Python 3.8+, standard library only — nothing to install).

    python server.py            -> http://localhost:8000

Endpoints
  GET  /                 test page (JavaScript parser runs in the browser)
  POST /api/parse        {"input": "<url or query>", "referrer": "...", "location_id": "..."}
                         -> Python parser result + POS mapping (Lead Source / Lead Channel / note)
  POST /api/capture      parse + map + save to a local SQLite test database
                         (simulates the API hub writing to the POS)
  GET  /api/records      last 10 saved records
  GET  /api/unmapped     values no mapping rule recognised, most frequent first (review queue)
  GET  /api/mapping      rules version and the POS lists in use
"""
import json
import os
import sqlite3
import sys
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER_DIR = os.path.join(os.path.dirname(HERE), "utm-parser")  # single source of truth
sys.path.insert(0, PARSER_DIR)

from utm_mapping import MappingConfigError, load_mapper  # noqa: E402
from utm_parser import TOUCH_TYPES, parse_utm  # noqa: E402

MAX_BODY = 64 * 1024
DB_PATH = os.path.join(HERE, "utm_test.db")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
COLUMNS = ["touch_type", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
           "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
           "click_ids_json", "extra_params_json", "landing_url", "referrer", "location_id", "captured_at",
           "pos_lead_source", "pos_lead_channel", "mapped", "mapping_rule_id", "rules_version"]


class BadRequest(Exception):
    pass


def connect():
    """Connection that is committed *and* closed (sqlite3's own context manager only commits)."""
    return closing(sqlite3.connect(DB_PATH))


def _col_type(c):
    return "INTEGER" if c == "mapped" else "TEXT"


def init_db():
    with connect() as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS utm_attribution (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   + ", ".join(f"{c} {_col_type(c)}" for c in COLUMNS)
                   + ", received_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        # Upgrade test databases created before the POS mapping columns existed
        existing = {row[1] for row in db.execute("PRAGMA table_info(utm_attribution)")}
        for c in COLUMNS:
            if c not in existing:
                db.execute(f"ALTER TABLE utm_attribution ADD COLUMN {c} {_col_type(c)}")


def parsed_dict(r):
    d = {k: getattr(r, k) for k in ["source", "medium", "campaign", "term", "content", "campaign_id",
                                     "source_platform", "creative_format", "marketing_tactic"]}
    d.update(click_ids=r.click_ids, extra=r.extra, has_utm=r.has_utm)
    return d


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest("invalid Content-Length")
        if length > MAX_BODY:
            raise BadRequest("request body too large")
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise BadRequest("invalid JSON")
        if not isinstance(body, dict):
            raise BadRequest("JSON body must be an object")
        return body

    @staticmethod
    def _bool(body, key, default):
        v = body.get(key, default)
        if not isinstance(v, bool):
            raise BadRequest(f"'{key}' must be true or false")
        return v

    def _parse(self, body):
        inp = body.get("input", "")
        if not isinstance(inp, str):
            raise BadRequest("'input' must be a string")
        return parse_utm(inp, lowercase=self._bool(body, "lowercase", True),
                         include_click_ids=self._bool(body, "include_click_ids", True))

    def _optional_str(self, body, key):
        v = body.get(key)
        if v is not None and not isinstance(v, str):
            raise BadRequest(f"'{key}' must be a string")
        return v or None

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/utm-parser/utm_parser.js":
            with open(os.path.join(PARSER_DIR, "utm_parser.js"), "rb") as f:
                return self._send(200, f.read(), "text/javascript; charset=utf-8")
        if self.path == "/api/records":
            with connect() as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT * FROM utm_attribution ORDER BY id DESC LIMIT 10").fetchall()
            return self._send(200, [dict(r) for r in rows])
        if self.path == "/api/unmapped":
            with connect() as db:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT utm_source, utm_medium, utm_campaign, COUNT(*) AS count, MAX(received_at) AS last_seen "
                                  "FROM utm_attribution WHERE mapped = 0 GROUP BY utm_source, utm_medium, utm_campaign "
                                  "ORDER BY count DESC, last_seen DESC LIMIT 50").fetchall()
            return self._send(200, [dict(r) for r in rows])
        if self.path == "/api/mapping":
            try:
                m = load_mapper()
            except MappingConfigError as e:
                return self._send(500, {"error": "mapping configuration is invalid", "details": str(e).splitlines()})
            return self._send(200, {"rules_version": m.version, "rule_count": len(m.rules),
                                    "lead_sources": m.config["lead_sources"], "lead_channels": m.config["lead_channels"],
                                    "fallback": m.config["fallback"], "pos_fields_touch": m.config.get("pos_fields_touch", "first")})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = self._json_body()
            r = self._parse(body)
            touch = body.get("touch", "last")
            if touch not in TOUCH_TYPES:
                raise BadRequest("'touch' must be 'first' or 'last'")
            ctx = {"landing_url": body.get("input"), "touch": touch,
                   "referrer": self._optional_str(body, "referrer"),
                   "location_id": self._optional_str(body, "location_id")}
        except BadRequest as e:
            return self._send(400, {"error": str(e)})
        try:
            mapper = load_mapper()
        except MappingConfigError as e:
            return self._send(500, {"error": "mapping configuration is invalid", "details": str(e).splitlines()})
        p = r.to_api_payload(**ctx)
        # The tester sends a single link, so it stands in for both first and last touch
        pos = mapper.build_pos_record(last_touch=p, first_touch=p)
        if self.path == "/api/parse":
            return self._send(200, {"parsed": parsed_dict(r), "errors": r.validate(), "payload": p, "pos": pos})
        if self.path == "/api/capture":
            if not r.has_attribution and not ctx["referrer"]:
                return self._send(422, {"errors": ["nothing to record: no UTMs, click IDs or referrer"]})
            m = pos["mapping"]
            p.update(pos_lead_source=pos["lead_source"], pos_lead_channel=pos["lead_channel"],
                     mapped=int(m["mapped"]), mapping_rule_id=m["rule_id"], rules_version=m["rules_version"])
            p["click_ids_json"] = json.dumps(p.pop("click_ids"))
            p["extra_params_json"] = json.dumps(p.pop("extra_params"))
            with connect() as db, db:
                cur = db.execute(f"INSERT INTO utm_attribution ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                                 [p.get(c) for c in COLUMNS])
            return self._send(201, {"id": cur.lastrowid, "saved": p, "pos": pos, "warnings": r.validate()})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


if __name__ == "__main__":
    init_db()
    print(f"UTM test server running at http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
