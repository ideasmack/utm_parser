UTM Parser Tester
=================
Requirements: Python 3.8 or later (nothing else to install).
Keep this folder next to ../utm-parser: the tester uses those parser files directly
(utm_parser.py on the server, utm_parser.js in the page), so there is only one copy of each.

Windows: double-click start.bat (opens your browser at http://localhost:8000)
Mac/Linux: run ./start.sh, then open http://localhost:8000

Paste a link or UTM string and click Parse. You'll see:
  - JavaScript parser results (runs in the browser, same code as the website snippet)
  - Python parser results (runs on the local server, same code as the API hub)
  - Whether the two match
  - The JSON payload that would go to the API hub
"Send to test database" validates and saves the record to utm_test.db (SQLite),
simulating the API hub writing to the POS. Delete utm_test.db to reset.

Tip: you can open index.html directly without the server; only the JavaScript column works then.
Run the tests: cd ../utm-parser && python3 -m unittest -v && node test_utm_parser.js
To use another port: python server.py 8080

POS mapping (Lead Source / Lead Channel)
----------------------------------------
The "POS mapping" panel shows the two values the POS would receive, the rule that chose
them, and the note text. Optional Referrer / Location ID fields test rules that use them.
  utm-parser/pos_lists.json     values the POS accepts (PLACEHOLDERS - replace with the real lists)
  utm-parser/mapping_rules.csv  the rules; instructions are in the comment rows at the top
Edit either file and click Parse again: the server reloads them automatically. If an edit
is invalid (for example a Lead Source that is not in pos_lists.json), the panel lists every problem.
Links no rule recognises get Unknown / Unmapped and appear in the "Unmapped review queue"
once saved with "Send to test database".
