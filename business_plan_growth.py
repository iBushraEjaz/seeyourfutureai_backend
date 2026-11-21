import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from business_result import (
    _band_for,
    _match_group,
    _resolve_category,
    _smart_score,
    _role_planets,
    _ROLE_FLAVOR,
    _ROLE_CHALLENGE,
)

router = APIRouter()

_DATA_PATH = Path(__file__).with_name("business_data.json")


def _load_data() -> dict:
    with _DATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load_data()
_GROWTH_PLANS: Dict[str, dict] = _DATA.get("growth_plans", {})

_STEP_MAP = {
    "month1_week1_step1": "week_1_step_1",
    "month1_week1_step2": "week_1_step_2",
    "month1_week2_step1": "week_2_step_1",
    "month1_week2_step2": "week_2_step_2",
    "month1_week3_step1": "week_3_step_1",
    "month1_week3_step2": "week_3_step_2",
    "month1_week4_step1": "week_4_step_1",
    "month1_week4_step2": "week_4_step_2",
    "month2_week1_step1": "week_5_step_1",
    "month2_week1_step2": "week_5_step_2",
    "month2_week2_step1": "week_6_step_1",
    "month2_week2_step2": "week_6_step_2",
    "month2_week3_step1": "week_7_step_1",
    "month2_week3_step2": "week_7_step_2",
    "month2_week4_step1": "week_8_step_1",
    "month2_week4_step2": "week_8_step_2",
}


def _sanitize(text: str) -> str:
    return text.replace("-", " ")


_BAND_RE = re.compile(r"\b(High|Mid|Low):\s*")


def _band_line(line4: str, band: str) -> str:
    parts = _BAND_RE.split(line4)
    for idx in range(1, len(parts) - 1, 2):
        if parts[idx] == band:
            return parts[idx + 1].strip()
    return line4.strip()


def _render_plan(
    plan_data: dict,
    band: str,
    role: str,
    role_planets: List[str],
) -> Dict[str, str]:
    planet = role_planets[0] if role_planets else ""
    flavor = _ROLE_FLAVOR.get(planet, "")
    challenge = _ROLE_CHALLENGE.get(planet, "")

    out: Dict[str, str] = {}
    for json_key, response_key in _STEP_MAP.items():
        lines = plan_data.get(json_key, [])
        if isinstance(lines, str):
            lines = [lines]
        lines = list(lines[:4])
        while len(lines) < 4:
            lines.append("Keep taking steady steps and review your progress.")

        rendered = []
        for idx, line in enumerate(lines):
            line = str(line)
            if idx == 2 and flavor:
                line = f"For {role}, lean on {flavor}."
            elif idx == 3:
                line = _band_line(line, band)
                if flavor:
                    line = f"{line} Keep {challenge} in mind this week."
            rendered.append(_sanitize(line.replace("[business]", role)))
        out[response_key] = "\n".join(rendered)
    return out


class BusinessGrowthRequest(BaseModel):
    dob: str
    category: str
    business: Optional[str] = ""


class BusinessGrowthResponse(BaseModel):
    week_1_step_1: str
    week_1_step_2: str
    week_2_step_1: str
    week_2_step_2: str
    week_3_step_1: str
    week_3_step_2: str
    week_4_step_1: str
    week_4_step_2: str
    week_5_step_1: str
    week_5_step_2: str
    week_6_step_1: str
    week_6_step_2: str
    week_7_step_1: str
    week_7_step_2: str
    week_8_step_1: str
    week_8_step_2: str


@router.post("/business-growth", response_model=BusinessGrowthResponse)
def business_growth(req: BusinessGrowthRequest):
    matched = _match_group(req.dob)
    if matched is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid date of birth. Use YYYY-MM-DD or DD-MM-YYYY.",
        )

    sign, group = matched
    texts_key: str = group.get("texts_key", sign)
    all_categories: Dict[str, int] = group.get("all_categories", {})
    top5_raw: List[dict] = group.get("top5", [])

    role = (req.business or "").strip() or (req.category or "").strip()

    resolved = _resolve_category(all_categories, req.category or "")
    if resolved is None:
        resolved = _resolve_category(all_categories, role)
    if resolved is None:
        resolved = (
            top5_raw[0]["category"]
            if top5_raw
            else next(iter(all_categories), "")
        )

    percent = _smart_score(sign, group, resolved, role)
    band = _band_for(percent)

    role_planets = _role_planets(role, resolved)

    plan_data = _GROWTH_PLANS.get(texts_key) or {}
    if not plan_data:
        raise HTTPException(
            status_code=500,
            detail="Growth plan data not available.",
        )

    plan = _render_plan(plan_data, band, role, role_planets)
    return BusinessGrowthResponse(**plan)