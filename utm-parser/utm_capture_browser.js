/**
 * utm_capture_browser.js — drop-in website snippet (load after utm_parser.js)
 *
 * Captures UTMs and ad click IDs on landing, keeps first-touch and last-touch copies
 * for 90 days, and injects them into every form as hidden fields so they reach the
 * API with the lead/booking.
 *
 * Hidden fields added per touch (prefix "first_" / "last_"):
 *   utm_source ... utm_marketing_tactic, click_ids (JSON), landing_url, referrer
 * The API hub rebuilds each touch with utm_mapping.payload_from_form() and maps it to POS values.
 */
(function () {
  'use strict';
  var KEY_FIRST = 'utm_first_touch', KEY_LAST = 'utm_last_touch', TTL_DAYS = 90;
  var UTM_KEYS = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
                  'utm_source_platform', 'utm_creative_format', 'utm_marketing_tactic'];

  function save(key, data) {
    try {
      localStorage.setItem(key, JSON.stringify({ data: data, expires: Date.now() + TTL_DAYS * 864e5 }));
    } catch (e) { /* storage blocked: fall through silently */ }
  }
  function load(key) {
    try {
      var item = JSON.parse(localStorage.getItem(key));
      if (!item || item.expires < Date.now()) { localStorage.removeItem(key); return null; }
      return item.data;
    } catch (e) { return null; }
  }

  if (typeof UtmParser === 'undefined') {
    if (window.console) console.warn('utm_capture_browser.js: load utm_parser.js first');
    return;
  }

  var parsed;
  try { parsed = UtmParser.parse(window.location.href); } catch (e) { parsed = null; }
  // Visits from another site (e.g. organic Google) count even without UTMs; moving between
  // our own pages does not
  function externalReferrer() {
    try {
      var ref = document.referrer ? new URL(document.referrer).hostname : '';
      return !!ref && ref !== window.location.hostname;
    } catch (e) { return false; }
  }

  // Record visits with UTMs, only a click ID (e.g. Google Ads auto-tagging: ?gclid=...),
  // or an external referrer
  if (parsed && (UtmParser.hasAttribution(parsed) || externalReferrer())) {
    var ctx = { landing_url: window.location.href.split('#')[0], referrer: document.referrer || null };
    if (!load(KEY_FIRST)) save(KEY_FIRST, UtmParser.toApiPayload(parsed, Object.assign({ touch: 'first' }, ctx)));
    save(KEY_LAST, UtmParser.toApiPayload(parsed, Object.assign({ touch: 'last' }, ctx)));
  }

  // Expose for custom JS / API calls
  window.getUtmAttribution = function () {
    return { first_touch: load(KEY_FIRST), last_touch: load(KEY_LAST) };
  };

  function setField(form, name, value) {
    var input = form.querySelector('input[name="' + name + '"]');
    if (!input) { input = document.createElement('input'); input.type = 'hidden'; input.name = name; form.appendChild(input); }
    input.value = value == null ? '' : value;
  }

  function fillForm(form) {
    if (!form || form.tagName !== 'FORM') return;
    var attr = window.getUtmAttribution();
    [['first', attr.first_touch], ['last', attr.last_touch]].forEach(function (pair) {
      var prefix = pair[0] + '_', data = pair[1];
      if (!data) return;
      UTM_KEYS.forEach(function (k) { setField(form, prefix + k, data[k]); });
      var ids = data.click_ids || {};
      setField(form, prefix + 'click_ids', Object.keys(ids).length ? JSON.stringify(ids) : '');
      setField(form, prefix + 'landing_url', data.landing_url);
      setField(form, prefix + 'referrer', data.referrer);
    });
  }

  // Normal and requestSubmit() submissions fire the submit event
  document.addEventListener('submit', function (evt) { fillForm(evt.target); }, true);

  // form.submit() does NOT fire the submit event, so wrap it
  var nativeSubmit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () {
    try { fillForm(this); } catch (e) { /* never block the submission */ }
    return nativeSubmit.apply(this, arguments);
  };
})();
