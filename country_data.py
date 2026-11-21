import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Words/phrases that must never reach the user in response text.
_FORBIDDEN_WORDS = (
    "evera",
    "zodiac",
    "astrolog",
    "aries",
    "taurus",
    "gemini",
    "cancer",
    "leo",
    "virgo",
    "libra",
    "scorpio",
    "sagittarius",
    "capricorn",
    "aquarius",
    "pisces",
    "birth",
    "afghanistan",
    "syria",
    "sweden",
)

# Countries removed from the pool per product decision; never supported.
_UNSUPPORTED_CODES = {"AF", "SY", "SE"}

# Accepted input aliases -> ISO code. Dubai/Scotland/Bali are not countries in the picker.
_ALIASES = {
    "usa": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "uae": "AE",
    "turkey": "TR",
    "t\u00fcrkiye": "TR",
    "turkiye": "TR",
}

_EVERA_PHRASE = "Evera match score"
_EVERA_REPLACEMENT = "match score"


class CountryData:
    def __init__(self):
        self._groups: List[Dict[str, Any]] = []
        self._group_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._countries: List[Dict[str, str]] = []
        self._country_by_code: Dict[str, Dict[str, Any]] = {}
        self._code_to_name: Dict[str, str] = {}
        self._name_to_code: Dict[str, str] = {}
        self._meta: Dict[str, Any] = {}
        self._scoring_version: str = ""
        self._load()

    # ------------------------------------------------------------ loading
    def _load(self):
        path = Path(__file__).with_name("country.txt")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return

        # country.txt is a single JSON object.
        data = json.loads(text)
        if not isinstance(data, dict):
            return

        self._meta = data.get("meta", {}) or {}
        self._scoring_version = str(self._meta.get("scoringVersion", ""))

        # countries is an OBJECT keyed by ISO code (not a list).
        raw_countries = self._meta_get_countries(data)
        countries = []
        for entry in raw_countries.values():
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("code", "")).upper()
            name = entry.get("name", code)
            if not code:
                continue
            profile = {
                "code": code,
                "name": name,
                "flag": entry.get("flag", ""),
                "headline": entry.get("headline", ""),
                "question1": entry.get("question1", {}) or {},
                "question2": entry.get("question2", {}) or {},
            }
            countries.append(profile)

        self._countries = [{"code": c["code"], "name": c["name"]} for c in countries]
        for c in countries:
            code = c["code"]
            name = c["name"]
            self._country_by_code[code] = c
            self._code_to_name[code] = name
            self._name_to_code[name.lower()] = code
            self._name_to_code[code.lower()] = code

        # groups: 120 entries, every country ranked in every group.
        for g in data.get("groups", []) or []:
            if not isinstance(g, dict):
                continue
            group_key = g.get("birthDateGroup")
            if not group_key:
                continue
            start_str, end_str = str(group_key).split("_to_")
            start_md = self._parse_month_day(start_str)
            end_md = self._parse_month_day(end_str)

            ranking = []
            for r in g.get("ranking", []) or []:
                if not isinstance(r, dict):
                    continue
                r_code = str(r.get("countryCode", "")).upper()
                if r_code:
                    ranking.append({
                        "rank": int(r.get("rank", 0)),
                        "countryCode": r_code,
                        "score": int(r.get("score", 0)),
                        "tier": str(r.get("tier", "")),
                        "scoreSource": str(r.get("scoreSource", "")),
                    })

            group_data = {
                "groupKey": group_key,
                "label": g.get("groupLabel", group_key),
                "start_md": start_md,
                "end_md": end_md,
                "lowerMatchReason": g.get("lowerMatchReason", ""),
                "ranking": ranking,
                "scoringVersion": self._scoring_version,
            }
            self._groups.append(group_data)

            idx: Dict[str, Dict[str, Any]] = {}
            for r in ranking:
                idx[r["countryCode"]] = r
            self._group_index[group_key] = idx

        self._sanitize_text_in_place()
        self._assert_no_forbidden()

    @staticmethod
    def _meta_get_countries(data: Dict[str, Any]) -> Dict[str, Any]:
        countries = data.get("countries")
        return countries if isinstance(countries, dict) else {}

    def _sanitize_text_in_place(self):
        """Strip banned brand wording from every loaded text field (in place)."""
        self._replace_strings_in_place(self._meta)
        for c in self._country_by_code.values():
            self._replace_strings_in_place(c)
        for g in self._groups:
            self._replace_strings_in_place(g)

    @staticmethod
    def _replace_strings_in_place(obj: Any) -> Any:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                obj[k] = CountryData._replace_strings_in_place(v)
            return obj
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                obj[i] = CountryData._replace_strings_in_place(v)
            return obj
        if isinstance(obj, str):
            return obj.replace(_EVERA_PHRASE, _EVERA_REPLACEMENT)
        return obj

    def _assert_no_forbidden(self):
        """Log an error if any banned word appears as a standalone word in loaded data."""
        import re
        haystack = json.dumps(
            {"meta": self._meta, "countries": list(self._country_by_code.values()),
             "groups": self._groups},
            ensure_ascii=False,
        )
        for word in _FORBIDDEN_WORDS:
            if re.search(r"\b" + re.escape(word) + r"\b", haystack, re.IGNORECASE):
                print(
                    "ERROR: forbidden word {!r} found in loaded country data".format(word),
                    file=sys.stderr,
                )

    # ------------------------------------------------------------ dates
    @staticmethod
    def _parse_month_day(s: str) -> Tuple[int, int]:
        """Parse '21-MAR' -> (3, 21)."""
        s = s.strip().upper()
        day_str, mon_str = s.split("-")
        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        return (month_map.get(mon_str[:3], 1), int(day_str))

    @staticmethod
    def _date_in_range(month: int, day: int, start_md: Tuple[int, int],
                       end_md: Tuple[int, int]) -> bool:
        """Check if (month, day) falls within [start_md, end_md], handling year wrap."""
        sm, sd = start_md
        em, ed = end_md
        if (sm, sd) <= (em, ed):
            return (sm, sd) <= (month, day) <= (em, ed)
        # wraps over new year (e.g. 28-FEB_to_02-MAR stays inside the year here,
        # but keep the wrap rule for any cross-year groups).
        return (month, day) >= (sm, sd) or (month, day) <= (em, ed)

    def find_group(self, dob: str) -> Optional[Dict[str, Any]]:
        """Parse dob and find matching group. Falls back to closest group if no exact match."""
        dt = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(dob.strip(), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
        month, day = dt.month, dt.day

        # First try exact match (ranges cover the full year, 29 Feb included).
        for g in self._groups:
            if self._date_in_range(month, day, g["start_md"], g["end_md"]):
                return g

        # Fallback: nearest group by day-of-year distance (29 Feb maps to day 60).
        target_doy = self._day_of_year(month, day)
        best_group = None
        best_dist = float("inf")
        for g in self._groups:
            start_doy = self._day_of_year(g["start_md"][0], g["start_md"][1])
            end_doy = self._day_of_year(g["end_md"][0], g["end_md"][1])
            if start_doy <= end_doy:
                group_center = (start_doy + end_doy) / 2
            else:
                group_center = ((start_doy + end_doy + 365) / 2) % 365
            dist = min(abs(target_doy - group_center), 365 - abs(target_doy - group_center))
            if dist < best_dist:
                best_dist = dist
                best_group = g
        return best_group

    @staticmethod
    def _day_of_year(month: int, day: int) -> int:
        """Convert month/day to day of year (1-365; 29 Feb maps to day 60)."""
        days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        return sum(days_in_month[:month]) + day

    # ------------------------------------------------------------ countries
    def normalize_country(self, value: str) -> Optional[str]:
        """Map any input (ISO code or name, case-insensitive) to an ISO code."""
        if not value:
            return None
        v = value.strip()
        v_upper = v.upper()

        if v_upper in _UNSUPPORTED_CODES:
            return None
        if v_upper in self._code_to_name:
            return v_upper

        v_lower = v.lower()
        if v_lower in self._name_to_code:
            return self._name_to_code[v_lower]
        if v_lower in _ALIASES:
            return _ALIASES[v_lower]
        return None

    def country_profile(self, code: str) -> Optional[Dict[str, Any]]:
        return self._country_by_code.get(code.upper())

    def get_country_result(self, dob: str, country: str) -> Tuple[Optional[Dict[str, Any]],
                                                                   Optional[Dict[str, Any]]]:
        """Return (group, ranking entry) or (None, error_reason)."""
        group = self.find_group(dob)
        if group is None:
            return None, f"No data available for date group matching {dob}"

        code = self.normalize_country(country)
        if code is None:
            return None, "Unsupported country"

        idx = self._group_index.get(group["groupKey"], {})
        if code not in idx:
            return None, "Unsupported country"

        return group, idx[code]

    def get_countries_list(self) -> List[Dict[str, str]]:
        items = [{"code": c["code"], "name": c["name"]} for c in self._countries]
        items.sort(key=lambda x: x["name"])
        return items

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "groupsLoaded": len(self._groups),
            "countriesLoaded": len(self._countries),
            "scoringVersion": self._scoring_version,
        }


# Singleton instance
_data: Optional[CountryData] = None


def get_data() -> CountryData:
    global _data
    if _data is None:
        _data = CountryData()
    return _data
