"""Natural-language → lineup-filter parsing for the DFS Lineup Explorer.

Hybrid design:
  * `parse_offline()` always works — no network, no API key. It recognises all
    30 MLB teams (nicknames, cities, abbreviations) and player names from the
    slate, plus intent cues ("high on", "love", "fade", "avoid", "stack", ...).
  * `parse_with_llm()` is an optional upgrade. When ANTHROPIC_API_KEY is set and
    the `anthropic` SDK is installed, it asks Claude to extract the same
    structured filters for richer, open-ended phrasing; otherwise we fall back
    to the offline parser.

Both return the same dict schema:
    {
      "include_players": [str, ...],   # exact names from the slate pool
      "exclude_players": [str, ...],
      "stack_teams":     [str, ...],   # team nicknames present in the slate
      "exclude_teams":   [str, ...],
      "min_stack_size":  int,          # >= 1
      "unknown_teams":   [str, ...],   # MLB teams named but not in this slate
      "notes":           [str, ...],
      "source":          "offline" | "claude",
    }
"""

from __future__ import annotations

import os
import re
import unicodedata
from difflib import get_close_matches

# --------------------------------------------------------------------------- #
# All 30 MLB teams: canonical nickname -> recognised aliases (lower-case).
# Canonical nicknames match the values used in the sim data's Team column.
# --------------------------------------------------------------------------- #
_MLB_TEAMS: dict[str, list[str]] = {
    "Diamondbacks": ["diamondbacks", "dbacks", "d-backs", "arizona", "ari"],
    "Braves": ["braves", "atlanta", "atl"],
    "Orioles": ["orioles", "baltimore", "bal"],
    "Red Sox": ["red sox", "boston", "bos", "bosox"],
    "Cubs": ["cubs", "chicago cubs", "chc"],
    "White Sox": ["white sox", "chicago white sox", "cws", "chw", "whitesox"],
    "Reds": ["reds", "cincinnati", "cin"],
    "Guardians": ["guardians", "cleveland", "cle", "guards"],
    "Rockies": ["rockies", "colorado", "col"],
    "Tigers": ["tigers", "detroit", "det"],
    "Astros": ["astros", "houston", "hou"],
    "Royals": ["royals", "kansas city", "kc", "kcr"],
    "Angels": ["angels", "anaheim", "laa", "halos"],
    "Dodgers": ["dodgers", "los angeles dodgers", "lad"],
    "Marlins": ["marlins", "miami", "mia"],
    "Brewers": ["brewers", "milwaukee", "mil"],
    "Twins": ["twins", "minnesota", "min"],
    "Mets": ["mets", "new york mets", "nym"],
    "Yankees": ["yankees", "new york yankees", "nyy", "yanks"],
    "Athletics": ["athletics", "as", "a's", "oakland", "oak", "ath"],
    "Phillies": ["phillies", "philadelphia", "phi", "phils"],
    "Pirates": ["pirates", "pittsburgh", "pit", "bucs"],
    "Padres": ["padres", "san diego", "sd", "sdp"],
    "Giants": ["giants", "san francisco", "sf", "sfg"],
    "Mariners": ["mariners", "seattle", "sea", "ms"],
    "Cardinals": ["cardinals", "st louis", "st. louis", "stl", "cards"],
    "Rays": ["rays", "tampa bay", "tampa", "tb", "tbr"],
    "Rangers": ["rangers", "texas", "tex"],
    "Blue Jays": ["blue jays", "toronto", "tor", "jays", "bluejays"],
    "Nationals": ["nationals", "washington", "wsh", "wsn", "nats"],
}

# Cue phrases that flip intent. Longer / more specific phrases first.
# Bare generic words like "on"/"into" are deliberately excluded — they fire
# inside phrases like "bearish on" and flip the intent back.
_POSITIVE_CUES = [
    "high on", "all in on", "all-in on", "up on", "in on", "bullish on",
    "build around", "love", "loving", "lock", "locking", "target", "targeting",
    "prioritize", "favor", "lean", "leaning", "like", "want",
]
_NEGATIVE_CUES = [
    "stay away from", "stay off", "steer clear of", "leave out", "low on",
    "bearish on", "don't want", "dont want", "do not want", "fade", "fading",
    "avoid", "exclude", "without", "hate", "off of", "off", "no ", "not ",
]

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _norm(text: str) -> str:
    """Lower-case and strip accents so 'José Suárez' matches 'jose suarez'."""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.lower()


def _blank_result(source: str) -> dict:
    return {
        "include_players": [], "exclude_players": [],
        "stack_teams": [], "exclude_teams": [],
        "min_stack_size": 1, "unknown_teams": [], "notes": [], "source": source,
    }


# --------------------------------------------------------------------------- #
# Offline parser
# --------------------------------------------------------------------------- #
def _team_alias_spans(norm_text: str) -> list[tuple[int, str]]:
    """Find team mentions; return (start_index, canonical_nickname)."""
    spans = []
    for canonical, aliases in _MLB_TEAMS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for m in re.finditer(rf"\b{re.escape(alias)}\b", norm_text):
                spans.append((m.start(), canonical))
                break  # one hit per alias is enough to flag the team
    return spans


def _player_spans(norm_text: str, player_names: list[str]) -> list[tuple[int, str]]:
    """Find player mentions by full name, or by a unique last name."""
    spans = []
    # Unique last-name index (skip ambiguous shared last names).
    last_index: dict[str, list[str]] = {}
    for name in player_names:
        last = _norm(name).split()[-1] if name.split() else ""
        if last:
            last_index.setdefault(last, []).append(name)

    matched = set()
    for name in player_names:
        nn = _norm(name)
        m = re.search(rf"\b{re.escape(nn)}\b", norm_text)
        if m:
            spans.append((m.start(), name))
            matched.add(name)

    for last, owners in last_index.items():
        if len(owners) == 1 and owners[0] not in matched and len(last) >= 4:
            m = re.search(rf"\b{re.escape(last)}\b", norm_text)
            if m:
                spans.append((m.start(), owners[0]))
    return spans


def _cue_spans(norm_text: str) -> list[tuple[int, str]]:
    """Find intent cues; return (start_index, kind) where kind in pos/neg/stack."""
    spans = []
    for cue in _NEGATIVE_CUES:
        for m in re.finditer(rf"\b{re.escape(cue)}", norm_text):
            spans.append((m.start(), "neg"))
    for cue in _POSITIVE_CUES:
        for m in re.finditer(rf"\b{re.escape(cue)}\b", norm_text):
            spans.append((m.start(), "pos"))
    for m in re.finditer(r"\bstack(?:s|ing|ed)?\b", norm_text):
        spans.append((m.start(), "stack"))
    return spans


def _detect_stack_size(norm_text: str) -> int | None:
    """Pull an explicit stack size from phrases like '5-man', 'stack of 4', '4+'."""
    patterns = [
        r"(\d+)\s*[- ]?man", r"stack(?:s|ing|ed)?\s+(?:of\s+)?(\d+)",
        r"(\d+)\s*[- ]?stack", r"(\d+)\s*piece", r"(\d+)\s*\+",
        # a bare number in the same clause as a stack cue, e.g. "stack the Angels 5"
        r"stack(?:s|ing|ed)?\b[^.,;]*?\b([2-6])\b",
    ]
    found = []
    for pat in patterns:
        for m in re.finditer(pat, norm_text):
            found.append(int(m.group(1)))
    for word, val in _NUM_WORDS.items():
        if re.search(rf"\b{word}[- ]?(?:man|stack|piece)\b", norm_text):
            found.append(val)
    found = [n for n in found if 2 <= n <= 6]
    return max(found) if found else None


def parse_offline(text: str, player_names: list[str], data_teams: set[str]) -> dict:
    """Rule-based parse of a free-text narrative into structured filters."""
    result = _blank_result("offline")
    if not text or not text.strip():
        return result

    norm_text = _norm(text)

    # Build an ordered marker stream so intent carries across an entity list,
    # e.g. "fade the Yankees and Aaron Judge" -> both negative.
    markers = (
        [(i, "cue", k) for i, k in _cue_spans(norm_text)]
        + [(i, "team", t) for i, t in _team_alias_spans(norm_text)]
        + [(i, "player", p) for i, p in _player_spans(norm_text, player_names)]
    )
    markers.sort(key=lambda m: m[0])

    explicit_size = _detect_stack_size(norm_text)
    any_stack_cue = any(k == "stack" for _, kind, k in markers if kind == "cue")

    polarity = 1       # default to positive intent
    stack_mode = False
    inc_p, exc_p, stack_t, exc_t, unknown = [], [], [], [], []

    for _, kind, value in markers:
        if kind == "cue":
            if value == "pos":
                polarity = 1
            elif value == "neg":
                polarity, stack_mode = -1, False
            elif value == "stack":
                polarity, stack_mode = 1, True
        elif kind == "team":
            if value not in data_teams:
                if value not in unknown:
                    unknown.append(value)
                continue
            if polarity < 0:
                exc_t.append(value)
            else:
                stack_t.append(value)
        elif kind == "player":
            (exc_p if polarity < 0 else inc_p).append(value)

    # De-dupe while preserving order.
    def _uniq(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    result["include_players"] = _uniq(inc_p)
    result["exclude_players"] = _uniq(exc_p)
    result["stack_teams"] = _uniq(stack_t)
    result["exclude_teams"] = _uniq([t for t in exc_t if t not in stack_t])
    result["unknown_teams"] = _uniq(unknown)

    if explicit_size:
        result["min_stack_size"] = explicit_size
    elif any_stack_cue and result["stack_teams"]:
        result["min_stack_size"] = 4  # "stack the Dodgers" with no number
    else:
        result["min_stack_size"] = 1

    if result["unknown_teams"]:
        result["notes"].append(
            "Not on this slate: " + ", ".join(result["unknown_teams"])
        )
    return result


# --------------------------------------------------------------------------- #
# Optional Claude API parser (activates only when configured)
# --------------------------------------------------------------------------- #
def llm_available() -> bool:
    """True if an API key is set and the anthropic SDK is importable."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "include_players": {"type": "array", "items": {"type": "string"}},
        "exclude_players": {"type": "array", "items": {"type": "string"}},
        "stack_teams": {"type": "array", "items": {"type": "string"}},
        "exclude_teams": {"type": "array", "items": {"type": "string"}},
        "min_stack_size": {"type": "integer"},
    },
    "required": [
        "include_players", "exclude_players", "stack_teams",
        "exclude_teams", "min_stack_size",
    ],
    "additionalProperties": False,
}

_LLM_SYSTEM = (
    "You translate a daily-fantasy-baseball narrative into structured lineup "
    "filters. Output ONLY the JSON schema fields.\n"
    "- include_players / exclude_players: player names the user is high on / "
    "wants to fade. Use the names verbatim as the user wrote them.\n"
    "- stack_teams: MLB team nicknames the user wants to stack or is high on.\n"
    "- exclude_teams: MLB team nicknames the user wants to fade/avoid entirely.\n"
    "- min_stack_size: an integer 1-6. If the user mentions a stack size (e.g. "
    "'5-man Dodgers stack') use it; if they say 'stack' with no number use 4; "
    "otherwise use 1.\n"
    "Use full team nicknames (e.g. 'Dodgers', 'Red Sox', 'White Sox')."
)


def parse_with_llm(text: str, player_names: list[str], data_teams: set[str]) -> dict:
    """Parse via Claude, then reconcile names/teams against the slate.

    Falls back to the offline parser on any error.
    """
    if not text or not text.strip() or not llm_available():
        return parse_offline(text, player_names, data_teams)

    try:
        import json
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            system=[{"type": "text", "text": _LLM_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema", "schema": _LLM_SCHEMA}},
            messages=[{"role": "user", "content": text}],
        )
        raw = next(b.text for b in response.content if b.type == "text")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — any failure → offline fallback
        out = parse_offline(text, player_names, data_teams)
        out["notes"].append(f"Claude parse unavailable ({exc}); used offline parser.")
        return out

    result = _blank_result("claude")
    result["min_stack_size"] = max(1, min(6, int(data.get("min_stack_size", 1))))
    result["include_players"] = _resolve_players(data.get("include_players", []), player_names)
    result["exclude_players"] = _resolve_players(data.get("exclude_players", []), player_names)
    stack_t, _ = _resolve_teams(data.get("stack_teams", []), data_teams)
    exc_t, unknown = _resolve_teams(data.get("exclude_teams", []), data_teams)
    result["stack_teams"] = stack_t
    result["exclude_teams"] = [t for t in exc_t if t not in stack_t]
    result["unknown_teams"] = unknown
    if unknown:
        result["notes"].append("Not on this slate: " + ", ".join(unknown))
    return result


def _resolve_players(names: list[str], pool: list[str]) -> list[str]:
    """Map free-form player names to exact slate names via fuzzy matching."""
    norm_pool = {_norm(p): p for p in pool}
    norm_keys = list(norm_pool)
    out = []
    for raw in names:
        key = _norm(raw)
        if key in norm_pool:
            out.append(norm_pool[key])
            continue
        match = get_close_matches(key, norm_keys, n=1, cutoff=0.82)
        if match:
            out.append(norm_pool[match[0]])
    # de-dupe preserving order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _resolve_teams(names: list[str], data_teams: set[str]) -> tuple[list[str], list[str]]:
    """Map team strings to canonical nicknames; split into in-slate / unknown."""
    in_slate, unknown = [], []
    for raw in names:
        canonical = _canonical_team(raw)
        if canonical is None:
            continue
        if canonical in data_teams:
            if canonical not in in_slate:
                in_slate.append(canonical)
        elif canonical not in unknown:
            unknown.append(canonical)
    return in_slate, unknown


def _canonical_team(raw: str) -> str | None:
    norm = _norm(raw).strip()
    for canonical, aliases in _MLB_TEAMS.items():
        if norm == _norm(canonical) or norm in aliases:
            return canonical
    return None
