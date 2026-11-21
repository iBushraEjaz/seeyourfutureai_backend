import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator


router = APIRouter(prefix="/visa", tags=["visa"])

_DATA_PATH = Path(__file__).with_name("visa_data.json")
_data: Optional[dict] = None
_countries_by_code: Dict[str, dict] = {}
_countries_by_name: Dict[str, str] = {}


def _load_data() -> dict:
    global _data, _countries_by_code, _countries_by_name
    if _data is not None:
        return _data
    text = _DATA_PATH.read_text(encoding="utf-8").strip()
    data = json.loads(text)
    countries = data.get("countries", {}) or {}
    for code, entry in countries.items():
        c_code = str(entry.get("code", code)).upper()
        c_name = entry.get("name", c_code)
        _countries_by_code[c_code] = entry
        _countries_by_name[c_name.lower()] = c_code
        _countries_by_name[c_code.lower()] = c_code
    _data = data
    return _data


def _normalize_code(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    v = value.strip()
    v_upper = v.upper()
    data = _load_data()
    if v_upper in _countries_by_code:
        return v_upper
    v_lower = v.lower()
    if v_lower in _countries_by_name:
        return _countries_by_name[v_lower]
    return None


def _condition_matches(condition: Optional[dict], info: dict) -> bool:
    if not condition:
        return True
    c_type = condition.get("type")
    if c_type == "age":
        age = info.get("age")
        if age is None:
            return True
        if "min" in condition and age < int(condition["min"]):
            return False
        if "max" in condition and age > int(condition["max"]):
            return False
        return True
    if c_type == "level":
        return info.get("level") in (condition.get("in") or [])
    if c_type == "masters_type":
        return info.get("masters_type") in (condition.get("in") or [])
    if c_type == "scholarship":
        return bool(info.get("scholarship_funded")) == bool(condition.get("in", [True])[0])
    # Unknown/informational condition -> include but mark conditional
    return True


class VisaCheckRequest(BaseModel):
    destination: str
    level: str
    age: Optional[int] = None
    masters_type: Optional[str] = None
    scholarship_funded: bool = False

    @field_validator("destination")
    @classmethod
    def dc_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("destination is required")
        return v

    @field_validator("level")
    @classmethod
    def level_valid(cls, v: str) -> str:
        v = v.strip().lower()
        allowed = {"bachelors", "taught_masters", "research_masters", "phd"}
        if v not in allowed:
            raise ValueError(f"level must be one of {sorted(allowed)}")
        return v


class VisaRequirement(BaseModel):
    id: str
    title: str
    subtitle: str
    applicability: str
    category: str
    source_url: Optional[str] = None
    last_checked: Optional[str] = None


class VisaCheckResponse(BaseModel):
    success: bool
    country: dict
    level: str
    requirements: List[VisaRequirement]
    conditional_requirements: List[VisaRequirement]
    scholarships: List[VisaRequirement]
    source_note: str


def _requirement_from_item(item: dict) -> VisaRequirement:
    return VisaRequirement(
        id=str(item.get("id", "")),
        title=str(item.get("title", "")),
        subtitle=str(item.get("subtitle", "")),
        applicability=str(item.get("applicability", "conditional")),
        category=str(item.get("category", "country")),
        source_url=item.get("source_url"),
        last_checked=item.get("last_checked"),
    )


@router.post("/visa-check", response_model=VisaCheckResponse)
def visa_check(req: VisaCheckRequest):
    data = _load_data()
    code = _normalize_code(req.destination)
    if code is None:
        raise HTTPException(status_code=400, detail="Unsupported destination country")

    entry = data["countries"].get(code)
    if not entry:
        raise HTTPException(status_code=400, detail="Destination country not found")

    levels = entry.get("levels_meta") or []
    info = {
        "age": req.age,
        "level": req.level,
        "masters_type": req.masters_type,
        "scholarship_funded": req.scholarship_funded,
    }

    requirements: List[VisaRequirement] = []
    conditional_requirements: List[VisaRequirement] = []

    for item in entry.get("requirements", []) or []:
        item_levels = item.get("levels") or []
        if item_levels and req.level not in item_levels:
            continue
        r = _requirement_from_item(item)
        if _condition_matches(item.get("condition"), info):
            requirements.append(r)

    for item in entry.get("conditional_requirements", []) or []:
        if not _condition_matches(item.get("condition"), info):
            continue
        conditional_requirements.append(_requirement_from_item(item))

    scholarships: List[VisaRequirement] = []
    if req.scholarship_funded:
        for item in entry.get("scholarships", []) or []:
            scholarships.append(_requirement_from_item(item))

    return VisaCheckResponse(
        success=True,
        country={"code": code, "name": entry.get("name", code)},
        level=req.level,
        requirements=requirements,
        conditional_requirements=conditional_requirements,
        scholarships=scholarships,
        source_note=entry.get("source_note") or data.get("meta", {}).get("source_note", ""),
    )


@router.get("/countries")
def visa_countries():
    data = _load_data()
    items = [
        {"code": entry.get("code", code), "name": entry.get("name", code)}
        for code, entry in data.get("countries", {}).items()
    ]
    items.sort(key=lambda x: x["name"])
    return items
