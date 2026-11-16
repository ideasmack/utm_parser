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
