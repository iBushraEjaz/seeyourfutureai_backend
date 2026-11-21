import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_DATA_PATH = Path(__file__).with_name("business_data.json")


def _load_data() -> dict:
    with _DATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load_data()
_DATE_RANGES: Dict[str, dict] = _DATA.get("date_ranges", {})
_TEXTS: Dict[str, dict] = _DATA.get("texts", {})

_GROUPS_PATH = Path(__file__).with_name("business_compatibility_data.json")


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_GROUPS_DATA: dict = _load_json(_GROUPS_PATH)
_GROUPS: Dict[str, dict] = _GROUPS_DATA.get("groups", {})

_MON = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_GROUP_KEY_RE = re.compile(r"^(\d\d)-([A-Z]{3})_to_(\d\d)-([A-Z]{3})$")


def _resolve_internal_key(dob: str) -> Optional[str]:
    md = _parse_dob(dob)
    if md is None:
        return None
    for key in _GROUPS:
        m = _GROUP_KEY_RE.match(key)
        if not m:
            continue
        sm = int(m.group(1)); smon = _MON[m.group(2)]
        em = int(m.group(3)); emon = _MON[m.group(4)]
        start = (smon, sm); end = (emon, em)
        if start <= end:
            if start <= md <= end:
                return key
        else:
            if md >= start or md <= end:
                return key
    return None

_DERIVED_FLOOR = 52
_DERIVED_CEILING_CAP = 70

# Planet affinity per category, based on the trades Lilly lists in data.txt.
_CAT_PLANETS: Dict[str, List[str]] = {
    "Software & IT Services": ["Mercury"],
    "Construction": ["Mars", "Saturn"],
    "Food Service": ["Mars", "Moon"],
    "Real Estate": ["Venus", "Saturn"],
    "Online Content": ["Mercury", "Venus"],
    "Wholesale Trade": ["Mercury"],
    "Food & Beverage Manufacturing": ["Mars", "Moon"],
    "Textile & Apparel": ["Venus"],
    "Wood, Paper & Printing": ["Mercury", "Mars"],
    "Chemicals, Metals & Machinery": ["Mars"],
    "Utilities": ["Saturn", "Sun"],
    "Horticulture & Floristry": ["Venus", "Moon"],
    "Quarrying & Extraction": ["Saturn"],
    "Retail Trade": ["Mercury"],
    "Transportation": ["Mercury", "Moon"],
    "Storage & Logistics": ["Saturn", "Mercury"],
    "Accommodation": ["Moon", "Venus"],
    "Forestry": ["Saturn", "Mars"],
    "Publishing & Writing": ["Mercury"],
    "Agriculture": ["Saturn", "Moon"],
    "Mining": ["Saturn"],
    "Finance": ["Jupiter", "Mercury"],
    "Insurance": ["Jupiter", "Saturn"],
    "Fishing & Aquaculture": ["Moon"],
    "Legal Services": ["Jupiter", "Mercury"],
    "Marketing Services": ["Mercury", "Venus"],
    "Design & Architecture": ["Venus", "Saturn"],
    "Scientific & Technical Services": ["Mercury", "Saturn"],
    "Veterinary Services": ["Moon", "Mars"],
    "Administrative & Support Services": ["Sun", "Mercury"],
    "Education": ["Jupiter", "Mercury"],
    "Health & Social Work": ["Moon", "Mercury"],
    "Arts & Entertainment": ["Venus"],
    "Sports & Recreation": ["Mars", "Sun"],
    "Other Personal & Community Services": ["Venus", "Moon"],
}

_CAT_PLANETS_LOWER = {k.lower(): v for k, v in _CAT_PLANETS.items()}

# First matching rule wins, so the specific entries come before the broad ones.
_ROLE_KEYWORDS = (
    (("salon", "barber", "beauty", "massage", "spa"), ["Venus", "Moon"]),
    (("restaurant", "kitchen", "butcher", "cook", "food"), ["Mars", "Moon"]),
    (("catering", "bakery"), ["Mars"]),
    (("cafe", "coffee"), ["Moon", "Venus"]),
    (("dairy", "beverage"), ["Moon"]),
    (("bar", "pub", "tavern"), ["Moon"]),
    (("law", "legal", "bank", "loan", "insurance"), ["Jupiter", "Mercury"]),
    (("school", "tutor", "coach", "education", "training"),
     ["Jupiter", "Mercury"]),
    (("software", "web", "data", "it support", "app"), ["Mercury"]),
    (("design", "architect", "interior"), ["Venus", "Saturn"]),
    (("clinic", "dental", "pharmacy", "physio", "health", "medical"),
     ["Moon", "Mars"]),
    (("welding", "metal", "machin", "construction", "plumb", "build"),
     ["Mars", "Saturn"]),
    (("farm", "agricult", "garden", "florist"), ["Saturn", "Venus"]),
    (("fish", "seafood"), ["Moon"]),
    (("transport", "logistic", "trucking", "delivery"),
     ["Mercury", "Moon"]),
    (("retail", "shop", "store", "wholesale", "trade"), ["Mercury"]),
    (("finance", "account", "invest", "tax"), ["Jupiter", "Mercury"]),
    (("gym", "sport", "fitness"), ["Mars", "Sun"]),
    (("photo", "art", "music", "theat", "entertain"), ["Venus"]),
    (("hotel", "accommodation", "tourism", "travel"), ["Moon", "Venus"]),
    (("real estate", "property", "rental"), ["Venus", "Saturn"]),
    (("security", "guard"), ["Mars", "Saturn"]),
    (("clean", "laundry"), ["Moon", "Saturn"]),
    (("mining", "quarr", "drill", "extract"), ["Saturn", "Mars"]),
    (("print", "publish", "writ", "content", "media"), ["Mercury"]),
    (("textile", "tailor", "cloth", "garment"), ["Venus"]),
    (("utility", "electric", "water", "energy", "solar"), ["Saturn", "Sun"]),
    (("consult", "advisory"), ["Jupiter", "Mercury"]),
    (("veterin", "pet"), ["Moon", "Mars"]),
)

_ROLE_FLAVOR = {
    "Mars": "drive, speed, and willingness to take the first step",
    "Sun": "confidence, visibility, and leadership",
    "Venus": "taste, warmth, and making customers feel welcome",
    "Mercury": "communication, quick learning, and trading skill",
    "Moon": "care, intuition, and reading what customers need",
    "Jupiter": "judgement, vision, and the ability to grow steadily",
    "Saturn": "discipline, patience, and building things that last",
}

_ROLE_CHALLENGE = {
    "Mars": "slowing down enough to check details before you rush",
    "Sun": "sharing credit and listening before you lead",
    "Venus": "setting fair prices instead of pleasing everyone",
    "Mercury": "finishing one idea before starting the next",
    "Moon": "keeping boundaries when customers ask for too much",
    "Jupiter": "keeping promises small enough to keep",
    "Saturn": "moving faster when the moment asks for it",
}

_INDEX: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
for _key in _DATE_RANGES.keys():
    _parts = _key.split("|")
    if len(_parts) != 3:
        continue
    _sign, _start, _end = _parts
    try:
        _sm, _sd = (int(x) for x in _start.split("-"))
        _em, _ed = (int(x) for x in _end.split("-"))
    except (ValueError, AttributeError):
        continue
    _INDEX.append(((_sm, _sd), (_em, _ed)))


def _parse_dob(dob: str) -> Optional[Tuple[int, int]]:
    if not dob:
        return None
    from datetime import datetime

    dob = dob.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y",
                "%Y/%m/%d", "%d.%m.%Y"):
        try:
            dt = datetime.strptime(dob, fmt)
            return (dt.month, dt.day)
        except ValueError:
            continue
    return None


def _in_range(md: Tuple[int, int], start: Tuple[int, int], end: Tuple[int, int]) -> bool:
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end


def _match_group(dob: str) -> Optional[Tuple[str, dict]]:
    md = _parse_dob(dob)
    if md is None:
        return None
    for key in _DATE_RANGES:
        parts = key.split("|")
        if len(parts) != 3:
            continue
        sign, start_str, end_str = parts
        try:
            sm, sd = (int(x) for x in start_str.split("-"))
            em, ed = (int(x) for x in end_str.split("-"))
        except ValueError:
            continue
        if _in_range(md, (sm, sd), (em, ed)):
            return sign, _DATE_RANGES[key]
    return None


def _resolve_category(all_categories: Dict[str, int], requested: str) -> Optional[str]:
    if requested:
        req = requested.strip()
        if req in all_categories:
            return req
        lowered = {name.lower(): name for name in all_categories}
        hit = lowered.get(req.lower())
        if hit:
            return hit
    return None


def _sanitize(text: str) -> str:
    return text.replace("-", " ")


def _band_for(percent: int) -> str:
    if percent >= 80:
        return "High"
    elif percent >= 65:
        return "Mid"
    else:
        return "Low"


def _new_band_key(percent: int) -> str:
    if percent >= 80:
        return "strong_match"
    if percent >= 65:
        return "good_match"
    if percent >= 50:
        return "uphill"
    return "not_worth_it"


def _stable_hash(*parts: object) -> int:
    payload = "|".join(str(p) for p in parts)
    return int(hashlib.md5(payload.encode("utf-8")).hexdigest()[:8], 16)


def _stable_adjustment(spread: int, *parts: object) -> int:
    return (_stable_hash(*parts) % (2 * spread + 1)) - spread


def _category_planets(category: str) -> List[str]:
    return _CAT_PLANETS_LOWER.get((category or "").strip().lower(), [])


def _affinity(planets: List[str], profile: Dict[str, float]) -> float:
    if not planets or not profile:
        return 0.0
    peak = max(profile.values())
    if peak <= 0:
        return 0.0
    return sum(profile.get(p, 0.0) for p in planets) / len(planets) / peak


def _keyword_hit(keyword: str, text: str) -> bool:
    if len(keyword) <= 3:
        return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None
    return keyword in text


_FORBIDDEN_BACKEND_RE = re.compile(
    r"\b(Aries|Taurus|Gemini|Cancer|Leo|Virgo|Libra|Scorpio|Sagittarius|Capricorn|Aquarius|Pisces|Mars|Venus|Mercury|Moon|Sun|Jupiter|Saturn|decan|ruler|birth|zodiac|January|February|March|April|May|June|July|August|September|October|November|December)\b",
    re.I,
)


def _clean_backend_text(text: str) -> str:
    if not text:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for s in sentences:
        t = s.strip()
        if not t:
            continue
        if _FORBIDDEN_BACKEND_RE.search(t):
            continue
        if re.search(r"^As a\s+\w+", t, re.I):
            continue
        t = re.sub(r"[-–—]", " ", t)
        t = re.sub(r" +", " ", t).strip()
        kept.append(t)
    return " ".join(kept)


def _role_planets(role: str, category: str = "") -> List[str]:
    if role:
        text = " " + role.strip().lower() + " "
        for keywords, planets in _ROLE_KEYWORDS:
            for keyword in keywords:
                if _keyword_hit(keyword, text):
                    return list(planets)
    return _category_planets(category)


def _top5_percent(group: dict, category: str) -> Optional[int]:
    if not category:
        return None
    target = category.strip().lower()
    for item in group.get("top5", []) or []:
        if str(item.get("category", "")).strip().lower() == target:
            return int(item.get("percent", 0))
    return None


def _smart_score(sign: str, group: dict, category: Optional[str], role: str) -> int:
    top5 = group.get("top5", []) or []

    real = _top5_percent(group, category or "")
    if real is not None:
        return real

    top5_scores = [int(item.get("percent", 0)) for item in top5]
    if top5_scores:
        ceiling = min(_DERIVED_CEILING_CAP, min(top5_scores) - 1)
    else:
        ceiling = _DERIVED_CEILING_CAP
    if ceiling < _DERIVED_FLOOR:
        ceiling = _DERIVED_FLOOR

    profile: Dict[str, float] = {}
    part_ruler = group.get("part_ruler") or ""
    if part_ruler:
        profile[part_ruler] = 1.0
    for item in top5:
        for planet in _category_planets(str(item.get("category", ""))):
            profile[planet] = profile.get(planet, 0.0) + 0.35
    if not profile:
        return _DERIVED_FLOOR

    cat_planets = _category_planets(category or "")
    cat_affinity = _affinity(cat_planets, profile)

    derived = _DERIVED_FLOOR + round((ceiling - _DERIVED_FLOOR) * cat_affinity)
    derived += _stable_adjustment(
        1, sign, group.get("texts_key", ""), category or ""
    )
    derived = max(_DERIVED_FLOOR, min(ceiling, derived))

    role_planets = _role_planets(role)
    if not role_planets:
        return derived

    role_affinity = _affinity(role_planets, profile)
    adjustment = round((role_affinity - cat_affinity) * 8)
    adjustment += _stable_adjustment(2, role)
    return max(_DERIVED_FLOOR, min(ceiling, derived + adjustment))


def _fill(text: str, business: str, percent: int) -> str:
    return text.replace("[%]", str(percent)).replace("[business]", business)


def _why_compatible(
    texts_key: str,
    band: str,
    category: str,
    role: str,
    percent: int,
    role_planets: List[str],
) -> str:
    text_data = _TEXTS.get(texts_key) or {}
    q1_map = text_data.get("Q1") or {}
    templates = q1_map.get(band) or q1_map.get("Mid") or []

    business = role or category
    planet = role_planets[0] if role_planets else ""
    flavor = _ROLE_FLAVOR.get(planet, "")
    challenge = _ROLE_CHALLENGE.get(planet, "")

    filled = [_fill(t, business, percent) for t in templates]

    if len(filled) >= 2 and flavor:
        if band == "High":
            filled[1] = (
                f"Your {business} work leans on {flavor}, "
                f"which is why the score reaches {percent}."
            )
        elif band == "Mid":
            filled[1] = (
                f"Your {business} work already shows {flavor}; "
                f"the part to practise is {challenge}."
            )
        else:
            filled[1] = (
                f"Your {business} work calls for more {challenge} "
                f"than your natural {flavor} gives easily."
            )

    return _sanitize("\n".join(filled))


def _best_options_text(
    top5: List[dict],
    texts_key: str,
    band: str,
    category: str,
    role: str,
    percent: int,
    role_planets: List[str],
) -> str:
    text_data = _TEXTS.get(texts_key) or {}
    q2_map = text_data.get("Q2") or {}
    templates = q2_map.get(band) or q2_map.get("Mid") or []

    names = [item.get("category", "") for item in top5 if isinstance(item, dict)]
    while len(names) < 3:
        names.append("this field")

    opt1, opt2, opt3 = names[0], names[1], names[2]
    business = role or category

    filled = []
    for t in templates:
        t = t.replace("[%]", str(percent))
        t = t.replace("[business]", business)
        t = t.replace("[option1]", opt1)
        t = t.replace("[option2]", opt2)
        t = t.replace("[option3]", opt3)
        filled.append(t)

    planet = role_planets[0] if role_planets else ""
    flavor = _ROLE_FLAVOR.get(planet, "")
    challenge = _ROLE_CHALLENGE.get(planet, "")

    if len(filled) >= 4 and flavor:
        if band == "High":
            filled[3] = (
                f"Put most of your energy into {business}, "
                f"because {flavor} carries it well."
            )
        elif band == "Mid":
            filled[3] = (
                f"Run {business} as a focused test for one month, "
                f"and watch whether {challenge} becomes easier."
            )
        else:
            filled[3] = (
                f"If you still want {business}, bring in a partner "
                f"who is strong at {challenge}."
            )

    return _sanitize("\n".join(filled))


class MatchBusinessRequest(BaseModel):
    dob: str
    name: Optional[str] = ""
    business: Optional[str] = ""
    category: Optional[str] = ""


class MatchBusinessResponse(BaseModel):
    compatibility: int
    best_options: List[dict]
    why_compatible: str
    best_options_text: str
    all_categories: Optional[Dict[str, int]] = None
    all_categories_source: Optional[Dict[str, bool]] = None
    best_roles: Optional[Dict[str, List[str]]] = None
    abilities: Optional[Dict[str, Any]] = None
    question1: Optional[Dict[str, Any]] = None
    question2: Optional[Dict[str, Any]] = None


@router.post("/match-business", response_model=MatchBusinessResponse)
def match_business(req: MatchBusinessRequest):
    matched = _match_group(req.dob)
    if matched is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid date of birth. Use YYYY-MM-DD or DD-MM-YYYY.",
        )

    sign, group = matched
    top5_raw: List[dict] = group.get("top5", [])
    texts_key: str = group.get("texts_key", sign)

    # --- new group data (date -> internal key) ---
    internal_key = _resolve_internal_key(req.dob)
    g = _GROUPS.get(internal_key) if internal_key else None
    all_categories: Dict[str, int] = g.get("all_categories", {}) if g else {}
    best_roles: Dict[str, List[str]] = g.get("best_roles", {}) if g else {}
    abilities: Dict[str, Any] = g.get("abilities", {}) if g else {}
    question1_raw: Dict[str, Any] = g.get("question1", {}) if g else {}
    question2_raw: Dict[str, Any] = g.get("question2", {}) if g else {}
    best_options_raw: List[dict] = g.get("best_options", []) if g else []

    resolved = _resolve_category(all_categories, req.category or "")
    if resolved is not None:
        display_category = resolved
    else:
        display_category = (
            top5_raw[0]["category"]
            if top5_raw
            else next(iter(all_categories), "this field")
        )

    role = (req.business or "").strip()

    # Fix 1: UNIFIED SCORE - use the all_categories percent for the selected
    # category so the ring, paragraph, and [score] replacement all match.
    if resolved is not None and resolved in all_categories:
        compatibility = int(all_categories[resolved])
    else:
        compatibility = _smart_score(sign, group, display_category, role)
    band = _band_for(compatibility)

    # Fix 3: resolve placeholder values once.
    good_at_names = [
        (a.get("name") if isinstance(a, dict) else str(a))
        for a in (abilities.get("good_at") or [])
    ]
    demand1 = good_at_names[0] if len(good_at_names) > 0 else "patience with people"
    demand2 = good_at_names[1] if len(good_at_names) > 1 else "money sense"

    # best options top 3, skipping the user's selected category.
    opt_cats: List[str] = []
    for opt in best_options_raw:
        if not isinstance(opt, dict):
            continue
        cat = opt.get("category", "")
        if cat and cat.lower() != display_category.lower():
            opt_cats.append(cat)
        if len(opt_cats) == 3:
            break
    option_a = opt_cats[0] if len(opt_cats) > 0 else "this field"
    option_b = opt_cats[1] if len(opt_cats) > 1 else "this field"
    option_c = opt_cats[2] if len(opt_cats) > 2 else "this field"

    role_a = (best_roles.get(option_a) or ["this role"])[0]
    role_b = (best_roles.get(option_b) or ["this role"])[0]
    role_c = (best_roles.get(option_c) or ["this role"])[0]

    # Fix 3: fill placeholders in the question bodies.
    def _fill(text: str) -> str:
        if not text:
            return text
        text = text.replace("[role]", role or "this business")
        text = text.replace("[score]", str(compatibility))
        text = text.replace("[demand1]", demand1)
        text = text.replace("[demand2]", demand2)
        text = text.replace("[optionA]", option_a)
        text = text.replace("[optionB]", option_b)
        text = text.replace("[optionC]", option_c)
        text = text.replace("[roleA]", role_a)
        text = text.replace("[roleB]", role_b)
        text = text.replace("[roleC]", role_c)
        return _clean_backend_text(text)

    q1_band = dict(question1_raw)  # full band map
    q2_band = dict(question2_raw)  # full band map
    cur_band = _new_band_key(compatibility)
    if cur_band in q1_band:
        q1_band[cur_band] = dict(q1_band[cur_band])
        q1_band[cur_band]["body"] = _fill(q1_band[cur_band].get("body", ""))
    if cur_band in q2_band:
        q2_band[cur_band] = dict(q2_band[cur_band])
        q2_band[cur_band]["body"] = _fill(q2_band[cur_band].get("body", ""))

    role_planets = _role_planets(role, display_category)
    why_compatible = _clean_backend_text(
        _why_compatible(
            texts_key, band, display_category, role, compatibility, role_planets
        )
    )
    best_options_text = _clean_backend_text(
        _best_options_text(
            top5_raw,
            texts_key,
            band,
            display_category,
            role,
            compatibility,
            role_planets,
        )
    )

    # Fix 2: best_options as List<Map> with real percents.
    best_options: List[dict] = []
    for opt in best_options_raw[:5]:
        if not isinstance(opt, dict):
            continue
        cat = opt.get("category", "")
        pct = opt.get("percent")
        if pct is None and cat in all_categories:
            pct = all_categories[cat]
        best_options.append({"category": cat, "percent": int(pct) if pct is not None else 0})

    # Merge new group data into the response alongside existing keys.
    extra: dict = {}
    if g:
        extra = {
            "all_categories": all_categories,
            "all_categories_source": g.get("all_categories_source", {}),
            "best_roles": best_roles,
            "abilities": abilities,
            "question1": q1_band,
            "question2": q2_band,
        }

    return MatchBusinessResponse(
        compatibility=compatibility,
        best_options=best_options,
        why_compatible=why_compatible,
        best_options_text=best_options_text,
        **extra,
    )