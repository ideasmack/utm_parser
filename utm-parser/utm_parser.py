"""
utm_parser.py — UTM parameter parser (Python 3.8+, standard library only)

Built on urllib.parse (Python's tested, RFC 3986-compliant URL parser) with a thin
UTM-specific layer for normalization, validation and API-payload shaping.
Use it server-side in the API hub / middleware to re-parse and validate whatever
the website sends before writing to the POS.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union
from urllib.parse import parse_qsl, urlsplit

UTM_FIELDS: Dict[str, str] = {
    "utm_source": "source",
    "utm_medium": "medium",
    "utm_campaign": "campaign",
    "utm_term": "term",
    "utm_content": "content",
    "utm_id": "campaign_id",
    "utm_source_platform": "source_platform",
    "utm_creative_format": "creative_format",
    "utm_marketing_tactic": "marketing_tactic",
}

CLICK_ID_FIELDS: Dict[str, str] = {
    "gclid": "google_click_id",
    "gbraid": "google_gbraid",
    "wbraid": "google_wbraid",
    "fbclid": "facebook_click_id",
    "msclkid": "microsoft_click_id",
    "ttclid": "tiktok_click_id",
    "li_fat_id": "linkedin_click_id",
}

TOUCH_TYPES = ("first", "last")
URL_MAX_LENGTH = 2000  # matches landing_url / referrer VARCHAR(2000)

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


@dataclass
class UtmResult:
    source: Optional[str] = None
    medium: Optional[str] = None
    campaign: Optional[str] = None
    term: Optional[str] = None
    content: Optional[str] = None
    campaign_id: Optional[str] = None
    source_platform: Optional[str] = None
    creative_format: Optional[str] = None
    marketing_tactic: Optional[str] = None
    click_ids: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def has_utm(self) -> bool:
        return any(getattr(self, f) for f in UTM_FIELDS.values())

    @property
    def has_attribution(self) -> bool:
        """True when there is anything worth recording (UTMs or a click ID)."""
        return self.has_utm or bool(self.click_ids)

    def validate(self) -> List[str]:
        """Return a list of errors; GA convention requires source, medium and campaign."""
        return [f"missing required utm_{f}" for f in ("source", "medium", "campaign") if not getattr(self, f)]

    def to_api_payload(self, **context: Any) -> Dict[str, Any]:
        """Flat dict ready to POST to the API hub or insert into a database."""
        touch = context.get("touch")
        return {
            "utm_source": self.source,
            "utm_medium": self.medium,
            "utm_campaign": self.campaign,
            "utm_term": self.term,
            "utm_content": self.content,
            "utm_id": self.campaign_id,
            "utm_source_platform": self.source_platform,
            "utm_creative_format": self.creative_format,
            "utm_marketing_tactic": self.marketing_tactic,
            "click_ids": dict(self.click_ids),
            "extra_params": dict(self.extra),
            "touch_type": touch if touch in TOUCH_TYPES else "last",
            "landing_url": _clip(context.get("landing_url")),
            "referrer": _clip(context.get("referrer")),
            "session_id": context.get("session_id"),
            "location_id": context.get("location_id"),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }


def _clip(value: Any) -> Optional[str]:
    return str(value)[:URL_MAX_LENGTH] if value else None


def _clean(value: Any, lowercase: bool, max_length: int) -> Optional[str]:
    if value is None:
        return None
    # '+' is already decoded to a space by parse_qsl; a literal '+' (sent as %2B)
    # must be preserved, so no '+' replacement here.
    v = _CONTROL_CHARS.sub("", str(value))
    v = _WHITESPACE.sub(" ", v).strip()
    if lowercase:
        v = v.lower()
    v = v[:max_length].strip()
    return v or None


def _to_pairs(source: Union[str, Mapping[str, Any], None]) -> List[tuple]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        return [(k, v) for k, v in source.items()]
    s = str(source).strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
        parts = urlsplit(s)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        # SPA-style hash routes: /#/page?utm_source=x
        if "?" in parts.fragment:
            pairs += parse_qsl(parts.fragment.split("?", 1)[1], keep_blank_values=True)
        return pairs
    return parse_qsl(s.split("?", 1)[1] if "?" in s else s, keep_blank_values=True)


def parse_utm(
    source: Union[str, Mapping[str, Any], None],
    *,
    lowercase: bool = True,
    max_length: int = 255,
    include_click_ids: bool = True,
    include_unknown_utm: bool = True,
) -> UtmResult:
    """Parse UTM values from a full URL, a query string, or a dict of parameters.

    Duplicate keys: the first occurrence wins (UTMs, click IDs and extras alike).
    Behaviour must stay identical to utm_parser.js (see test_parity.py).
    """
    result = UtmResult()
    for raw_key, raw_value in _to_pairs(source):
        key = str(raw_key).strip().lower()
        if key in UTM_FIELDS:
            attr = UTM_FIELDS[key]
            if getattr(result, attr) is None:  # first occurrence wins
                setattr(result, attr, _clean(raw_value, lowercase, max_length))
        elif include_click_ids and key in CLICK_ID_FIELDS:
            cleaned = _clean(raw_value, False, max_length)  # click IDs are case-sensitive
            if cleaned:
                result.click_ids.setdefault(CLICK_ID_FIELDS[key], cleaned)
        elif include_unknown_utm and key.startswith("utm_"):
            result.extra.setdefault(key, _clean(raw_value, lowercase, max_length))
    return result


if __name__ == "__main__":
    import json, sys

    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://www.mollymaid.com/book/?utm_source=Facebook&utm_medium=paid_social"
        "&utm_campaign=Spring%20Clean%202026&utm_content=video_a&fbclid=IwAR0AbC"
    )
    r = parse_utm(url)
    print(json.dumps({"valid": not r.validate(), "errors": r.validate(),
                      "payload": r.to_api_payload(landing_url=url)}, indent=2))
