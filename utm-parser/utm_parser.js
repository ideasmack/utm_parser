/**
 * utm_parser.js — UTM parameter parser (browser + Node.js, no dependencies)
 *
 * Built on the WHATWG URL / URLSearchParams standard (tested native parsers in every
 * modern browser and Node 10+), with a thin UTM-specific layer for normalization,
 * validation and API-payload shaping.
 *
 * Behaviour must stay identical to utm_parser.py (see test_parity.py).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.UtmParser = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // The five standard UTM parameters plus the GA4 additions
  const UTM_FIELDS = {
    utm_source: 'source',
    utm_medium: 'medium',
    utm_campaign: 'campaign',
    utm_term: 'term',
    utm_content: 'content',
    utm_id: 'campaign_id',
    utm_source_platform: 'source_platform',
    utm_creative_format: 'creative_format',
    utm_marketing_tactic: 'marketing_tactic',
  };

  // Ad-platform click IDs often travel with UTMs and are useful for attribution
  const CLICK_ID_FIELDS = {
    gclid: 'google_click_id',
    gbraid: 'google_gbraid',
    wbraid: 'google_wbraid',
    fbclid: 'facebook_click_id',
    msclkid: 'microsoft_click_id',
    ttclid: 'tiktok_click_id',
    li_fat_id: 'linkedin_click_id',
  };

  const TOUCH_TYPES = ['first', 'last'];
  const URL_MAX_LENGTH = 2000; // matches landing_url / referrer VARCHAR(2000)

  const DEFAULTS = {
    lowercase: true,        // normalize case so "Facebook" and "facebook" match in reporting
    maxLength: 255,         // fits a typical VARCHAR(255) column
    includeClickIds: true,
    includeUnknownUtm: true // keep any other utm_* keys under "extra"
  };

  // Truncate by Unicode code points (not UTF-16 units) so emoji are never split
  function truncate(v, max) {
    const chars = Array.from(v);
    return chars.length > max ? chars.slice(0, max).join('') : v;
  }

  function clean(value, opts) {
    if (value == null) return null;
    // Note: '+' is already decoded to a space by URLSearchParams; a literal '+'
    // (sent as %2B) must be preserved, so no '+' replacement here.
    let v = String(value)
      .replace(/[\u0000-\u001F\u007F]/g, '') // strip control characters
      .replace(/\s+/g, ' ')
      .trim();
    if (opts.lowercase) v = v.toLowerCase();
    v = truncate(v, opts.maxLength).trim();
    return v === '' ? null : v;
  }

  function toSearchParams(input) {
    if (input == null) return new URLSearchParams();
    if (input instanceof URLSearchParams) return input;
    if (typeof input === 'object') return new URLSearchParams(input);
    const s = String(input).trim();
    // Full URL
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
      const u = new URL(s);
      const params = new URLSearchParams(u.search);
      // Some SPAs/redirectors put the query after the hash: /#/page?utm_source=x
      const hashQ = u.hash.indexOf('?');
      if (hashQ !== -1) {
        new URLSearchParams(u.hash.slice(hashQ + 1)).forEach((v, k) => params.append(k, v));
      }
      return params;
    }
    // Bare query string ("?utm_source=x&..." or "utm_source=x&...")
    const q = s.indexOf('?');
    return new URLSearchParams(q === -1 ? s : s.slice(q + 1));
  }

  /**
   * Parse UTM values from a URL, query string, URLSearchParams or plain object.
   * Duplicate keys: the first occurrence wins (UTMs, click IDs and extras alike).
   */
  function parse(input, options) {
    const opts = Object.assign({}, DEFAULTS, options || {});
    const caseSensitive = Object.assign({}, opts, { lowercase: false });
    const params = toSearchParams(input);
    const result = {};
    Object.values(UTM_FIELDS).forEach((f) => { result[f] = null; });
    result.click_ids = {};
    result.extra = {};

    params.forEach((rawValue, rawKey) => {
      const key = rawKey.trim().toLowerCase(); // "UTM_Source" -> "utm_source"
      if (Object.prototype.hasOwnProperty.call(UTM_FIELDS, key)) {
        if (result[UTM_FIELDS[key]] == null) result[UTM_FIELDS[key]] = clean(rawValue, opts);
      } else if (opts.includeClickIds && Object.prototype.hasOwnProperty.call(CLICK_ID_FIELDS, key)) {
        // Click IDs are case-sensitive tokens: never lowercase them; skip empty values
        const name = CLICK_ID_FIELDS[key];
        const v = clean(rawValue, caseSensitive);
        if (v && !(name in result.click_ids)) result.click_ids[name] = v;
      } else if (opts.includeUnknownUtm && key.startsWith('utm_')) {
        if (!(key in result.extra)) result.extra[key] = clean(rawValue, opts);
      }
    });

    result.has_utm = Object.values(UTM_FIELDS).some((f) => result[f] != null);
    return result;
  }

  /** True when the input carries anything worth recording (UTMs or a click ID). */
  function hasAttribution(parsed) {
    return !!parsed.has_utm || Object.keys(parsed.click_ids || {}).length > 0;
  }

  /** Basic validation: GA convention requires source, medium and campaign. */
  function validate(parsed) {
    const errors = [];
    ['source', 'medium', 'campaign'].forEach((f) => {
      if (!parsed[f]) errors.push(`missing required utm_${f}`);
    });
    return { valid: errors.length === 0, errors };
  }

  function clip(v) { return v ? truncate(String(v), URL_MAX_LENGTH) : null; }

  /**
   * Shape a flat JSON payload for the API hub / database.
   * context: { landing_url, referrer, session_id, location_id, touch: 'first'|'last' }
   */
  function toApiPayload(parsed, context) {
    const ctx = context || {};
    return {
      utm_source: parsed.source,
      utm_medium: parsed.medium,
      utm_campaign: parsed.campaign,
      utm_term: parsed.term,
      utm_content: parsed.content,
      utm_id: parsed.campaign_id,
      utm_source_platform: parsed.source_platform,
      utm_creative_format: parsed.creative_format,
      utm_marketing_tactic: parsed.marketing_tactic,
      click_ids: Object.assign({}, parsed.click_ids),
      extra_params: Object.assign({}, parsed.extra),
      touch_type: TOUCH_TYPES.indexOf(ctx.touch) !== -1 ? ctx.touch : 'last',
      landing_url: clip(ctx.landing_url),
      referrer: clip(ctx.referrer),
      session_id: ctx.session_id || null,
      location_id: ctx.location_id || null,
      captured_at: new Date().toISOString(),
    };
  }

  return { parse, validate, toApiPayload, hasAttribution, UTM_FIELDS, CLICK_ID_FIELDS, TOUCH_TYPES };
});
