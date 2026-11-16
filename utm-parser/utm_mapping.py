"""
utm_mapping.py — map parsed UTM attribution onto the POS's fixed Lead Source / Lead Channel lists.

Runs server-side in the API hub, after utm_parser.py has cleaned the values:

    URL -> parse_utm() -> to_api_payload() -> Mapper.map() -> POS Lead Source + Lead Channel

Configuration is data, not code:
  pos_lists.json     the values the POS accepts, the fallback, which touch feeds the POS fields
  mapping_rules.csv  ordered rules (see the comment rows at the top of that file)

Every rule's outputs are checked against pos_lists.json when the files are loaded, so a typo in
the rules can never send an invalid value to the POS. Inputs from third-party marketers are never
trusted: anything no rule recognises gets the fallback values and mapped=False, which feeds the
"unmapped" review queue.
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from utm_parser import CLICK_ID_FIELDS, UTM_FIELDS, parse_utm

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_PATH = os.path.join(HERE, "mapping_rules.csv")
DEFAULT_CONFIG_PATH = os.path.join(HERE, "pos_lists.json")

NONE_TOKEN = "(none)"
REQUIRED_COLUMNS = ("rule_id", "priority", "lead_source", "lead_channel")
CONDITION_COLUMNS = ("source", "medium", "campaign", "click_id", "referrer")
ALL_COLUMNS = REQUIRED_COLUMNS + CONDITION_COLUMNS + ("location_id", "has_utm", "notes")
TOUCH_TYPES = ("first", "last")
UTM_KEYS = list(UTM_FIELDS)  # utm_source, utm_medium, ...


class MappingConfigError(ValueError):
    """Raised when pos_lists.json or mapping_rules.csv is invalid (all problems listed at once)."""


@dataclass(frozen=True)
class Rule:
    rule_id: str
    priority: int
    order: int  # row order in the file, breaks priority ties
    lead_source: str
    lead_channel: str
    conditions: Tuple[Tuple[str, Tuple[str, ...]], ...]
    has_utm: Optional[bool]
    locations: FrozenSet[str]

    def matches(self, facts: Mapping[str, Any]) -> bool:
        if self.has_utm is not None and facts["has_utm"] != self.has_utm:
            return False
        if self.locations and facts["location_id"] not in self.locations:
            return False
        return all(_matches(patterns, facts[column]) for column, patterns in self.conditions)


@dataclass(frozen=True)
class MappingResult:
    lead_source: str
    lead_channel: str
    mapped: bool              # False = fallback used, needs a rule
    rule_id: Optional[str]    # None when the fallback was used
    rules_version: str        # fingerprint of the rules + lists that produced this result


def _patterns(cell: str) -> Tuple[str, ...]:
    return tuple(p.strip().lower() for p in cell.split("|") if p.strip())


def _matches(patterns: Tuple[str, ...], value: Any) -> bool:
    """value is a string ('' when empty) or, for click_id, a frozenset of click ID names."""
    if isinstance(value, frozenset):
        if not value:
            return NONE_TOKEN in patterns
        return any(fnmatch.fnmatchcase(name, p) for p in patterns if p != NONE_TOKEN for name in value)
    if not value:
        return NONE_TOKEN in patterns
    return any(p != NONE_TOKEN and fnmatch.fnmatchcase(value, p) for p in patterns)


def _host(url: Any) -> str:
    if not url:
        return ""
    try:
        host = (urlsplit(str(url).strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _page(url: Any) -> str:
    """host + path only: query strings can carry personal data and don't belong in a POS note."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return ""
    return (parts.hostname or "") + (parts.path or "")


def _validate_config(config: Any) -> List[str]:
    if not isinstance(config, dict):
        return ["pos_lists.json must contain a JSON object"]
    errors = []
    for key in ("lead_sources", "lead_channels"):
        values = config.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
            errors.append(f"pos_lists.json: '{key}' must be a non-empty list of strings")
    fb = config.get("fallback")
    if not isinstance(fb, dict):
        errors.append("pos_lists.json: 'fallback' must have lead_source and lead_channel")
    elif not errors:
        if fb.get("lead_source") not in config["lead_sources"]:
            errors.append(f"pos_lists.json: fallback lead_source {fb.get('lead_source')!r} is not in lead_sources")
        if fb.get("lead_channel") not in config["lead_channels"]:
            errors.append(f"pos_lists.json: fallback lead_channel {fb.get('lead_channel')!r} is not in lead_channels")
    if config.get("pos_fields_touch", "first") not in TOUCH_TYPES:
        errors.append("pos_lists.json: 'pos_fields_touch' must be 'first' or 'last'")
    if not isinstance(config.get("internal_domains", []), list):
        errors.append("pos_lists.json: 'internal_domains' must be a list")
    return errors


def _parse_rules(rules_csv: str, config: Mapping[str, Any]) -> Tuple[List[Rule], List[str]]:
    reader = csv.DictReader(io.StringIO(rules_csv))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    errors = [f"mapping_rules.csv: missing column '{c}'" for c in REQUIRED_COLUMNS if c not in headers]
    errors += [f"mapping_rules.csv: unknown column '{h}' (typo?)" for h in headers if h and h not in ALL_COLUMNS]
    if errors:
        return [], errors

    sources, channels = set(config["lead_sources"]), set(config["lead_channels"])
    rules, seen = [], set()
    for order, raw in enumerate(reader):
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if not isinstance(v, list)}
        line = reader.line_num
        rule_id = row.get("rule_id", "")
        if not rule_id or rule_id.startswith("#"):
            continue  # blank or comment row
        where = f"mapping_rules.csv line {line} ({rule_id})"
        if rule_id in seen:
            errors.append(f"{where}: duplicate rule_id")
        seen.add(rule_id)
        try:
            priority = int(row.get("priority", ""))
        except ValueError:
            errors.append(f"{where}: priority must be a whole number")
            priority = 0
        if row.get("lead_source") not in sources:
            errors.append(f"{where}: lead_source {row.get('lead_source')!r} is not in pos_lists.json")
        if row.get("lead_channel") not in channels:
            errors.append(f"{where}: lead_channel {row.get('lead_channel')!r} is not in pos_lists.json")
        has_utm_cell = row.get("has_utm", "").lower()
        if has_utm_cell not in ("", "yes", "no"):
            errors.append(f"{where}: has_utm must be yes, no or blank")
        conditions = tuple((c, _patterns(row[c])) for c in CONDITION_COLUMNS if _patterns(row.get(c, "")))
        if not conditions:
            errors.append(f"{where}: rule has no conditions; catch-all values belong in pos_lists.json 'fallback'")
        rules.append(Rule(
            rule_id=rule_id, priority=priority, order=order,
            lead_source=row.get("lead_source", ""), lead_channel=row.get("lead_channel", ""),
            conditions=conditions,
            has_utm={"yes": True, "no": False}.get(has_utm_cell),
            locations=frozenset(p.strip() for p in row.get("location_id", "").split("|") if p.strip()),
        ))
    rules.sort(key=lambda r: (r.priority, r.order))
    return rules, errors


class Mapper:
    def __init__(self, rules: List[Rule], config: Mapping[str, Any], version: str):
        self.rules = rules
        self.config = config
        self.version = version
        self._internal = tuple(d.lower().lstrip(".") for d in config.get("internal_domains", []))

    @classmethod
    def from_text(cls, rules_csv: str, config: Any) -> "Mapper":
        errors = _validate_config(config)
        rules: List[Rule] = []
        if not errors:
            rules, errors = _parse_rules(rules_csv, config)
        if errors:
            raise MappingConfigError("\n".join(errors))
        digest = hashlib.sha256((rules_csv + json.dumps(config, sort_keys=True)).encode()).hexdigest()[:12]
        return cls(rules, config, digest)

    @classmethod
    def from_files(cls, rules_path: str = DEFAULT_RULES_PATH, config_path: str = DEFAULT_CONFIG_PATH) -> "Mapper":
        with open(config_path, encoding="utf-8") as f:
            try:
                config = json.load(f)
            except ValueError as e:
                raise MappingConfigError(f"pos_lists.json is not valid JSON: {e}")
        with open(rules_path, encoding="utf-8-sig", newline="") as f:  # -sig: tolerate Excel's BOM
            return cls.from_text(f.read(), config)

    # --- mapping -----------------------------------------------------------------------------
    def _facts(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        ref = _host(payload.get("referrer"))
        if ref and any(ref == d or ref.endswith("." + d) for d in self._internal):
            ref = ""  # moving between our own pages is not a referral
        return {
            "source": str(payload.get("utm_source") or "").lower(),
            "medium": str(payload.get("utm_medium") or "").lower(),
            "campaign": str(payload.get("utm_campaign") or "").lower(),
            "click_id": frozenset(k.lower() for k in (payload.get("click_ids") or {})),
            "referrer": ref,
            "has_utm": any(payload.get(k) for k in UTM_KEYS),
            "location_id": str(payload.get("location_id") or ""),
        }

    def map(self, payload: Mapping[str, Any]) -> MappingResult:
        """Map one touch (a to_api_payload() dict) to POS values."""
        facts = self._facts(payload)
        for rule in self.rules:
            if rule.matches(facts):
                return MappingResult(rule.lead_source, rule.lead_channel, True, rule.rule_id, self.version)
        fb = self.config["fallback"]
        return MappingResult(fb["lead_source"], fb["lead_channel"], False, None, self.version)

    # --- POS record --------------------------------------------------------------------------
    def build_pos_record(self, last_touch: Optional[Mapping[str, Any]],
                         first_touch: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        """
        What to send to the POS: the two structured fields plus a short human-readable note.
        The touch that feeds the structured fields is set by pos_fields_touch in pos_lists.json
        (falls back to the other touch if that one is missing).
        Store the full payloads in your own attribution table; this record is only for the POS.
        """
        touches = {"first": first_touch, "last": last_touch}
        preferred = self.config.get("pos_fields_touch", "first")
        chosen = preferred if touches[preferred] else ("last" if preferred == "first" else "first")
        if not touches[chosen]:
            fb = self.config["fallback"]
            result = MappingResult(fb["lead_source"], fb["lead_channel"], False, None, self.version)
        else:
            result = self.map(touches[chosen])
        return {
            "lead_source": result.lead_source,
            "lead_channel": result.lead_channel,
            "note": self.pos_note(result, chosen, first_touch, last_touch),
            "mapping": dict(asdict(result), touch=chosen),
        }

    def pos_note(self, result: MappingResult, chosen: str,
                 first_touch: Optional[Mapping[str, Any]], last_touch: Optional[Mapping[str, Any]]) -> str:
        lines = [f"Lead: {result.lead_source} / {result.lead_channel} "
                 f"({chosen} touch, rule {result.rule_id or 'none - UNMAPPED, please review'})"]
        for label, p in (("First touch", first_touch), ("Last touch", last_touch)):
            if not p:
                continue
            parts = [f"{k[4:]}={p[k]}" for k in UTM_KEYS if p.get(k)] or ["no UTM"]
            if p.get("click_ids"):
                parts.append("click IDs: " + ", ".join(sorted(p["click_ids"])))
            if _page(p.get("landing_url")):
                parts.append("landing: " + _page(p.get("landing_url")))
            if _host(p.get("referrer")):
                parts.append("referrer: " + _host(p.get("referrer")))
            if p.get("captured_at"):
                parts.append(str(p["captured_at"])[:10])
            lines.append(f"{label}: " + " · ".join(parts))
        note = "\n".join(lines)
        limit = int(self.config.get("note_max_length", 1000))
        return note if len(note) <= limit else note[: limit - 1] + "…"


# --- helpers for the API hub -----------------------------------------------------------------
_cache: Dict[Tuple[str, str], Tuple[Tuple[float, float], Mapper]] = {}
_cache_lock = threading.Lock()


def load_mapper(rules_path: str = DEFAULT_RULES_PATH, config_path: str = DEFAULT_CONFIG_PATH) -> Mapper:
    """Load the mapper, reloading automatically when either file changes (no restart needed).
    If an edited file is invalid, MappingConfigError is raised and nothing invalid is used."""
    key = (rules_path, config_path)
    mtimes = (os.path.getmtime(rules_path), os.path.getmtime(config_path))
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == mtimes:
            return cached[1]
        mapper = Mapper.from_files(rules_path, config_path)
        _cache[key] = (mtimes, mapper)
        return mapper


def payload_from_form(form: Mapping[str, Any], touch: str) -> Optional[Dict[str, Any]]:
    """
    Rebuild one touch's payload from the hidden fields utm_capture_browser.js adds to forms
    (first_utm_source, first_click_ids, first_landing_url, first_referrer, ...).
    Values come from the browser, so they are re-cleaned with the parser. Returns None when
    that touch carries no attribution.
    """
    if touch not in TOUCH_TYPES:
        raise ValueError("touch must be 'first' or 'last'")
    prefix = touch + "_"
    params: Dict[str, Any] = {k: form.get(prefix + k) for k in UTM_KEYS if form.get(prefix + k)}
    try:
        ids = json.loads(form.get(prefix + "click_ids") or "{}")
    except ValueError:
        ids = {}
    if isinstance(ids, dict):
        by_name = {v: k for k, v in CLICK_ID_FIELDS.items()}  # google_click_id -> gclid
        params.update({by_name[name]: value for name, value in ids.items() if name in by_name})
    parsed = parse_utm(params)
    if not parsed.has_attribution:
        return None
    return parsed.to_api_payload(touch=touch, landing_url=form.get(prefix + "landing_url"),
                                 referrer=form.get(prefix + "referrer"), location_id=form.get("location_id"))


if __name__ == "__main__":
    import sys

    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.mollymaid.com/book/?utm_source=FB&utm_medium=paid_social&utm_campaign=Spring"
    referrer = sys.argv[2] if len(sys.argv) > 2 else None
    payload = parse_utm(url).to_api_payload(landing_url=url, referrer=referrer)
    print(json.dumps(load_mapper().build_pos_record(payload, payload), indent=2, ensure_ascii=False))
