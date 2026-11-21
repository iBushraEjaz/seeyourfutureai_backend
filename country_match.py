from typing import Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from country_data import get_data


router = APIRouter(prefix="/country-match", tags=["country-match"])


class MatchRequest(BaseModel):
    name: str
    dob: str
    country: str

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("dob")
    @classmethod
    def dob_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("dob is required")
        return v

    @field_validator("country")
    @classmethod
    def country_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("country is required")
        return v


def _fill_placeholders(text: str, score: int, rank: int) -> str:
    return text.replace("{score}", str(score)).replace("{rank}", str(rank))


class TopCountry(BaseModel):
    rank: int
    code: str
    country: str
    score: int
    tier: str
    headline: str
    explanation: str


class AllCountryItem(BaseModel):
    rank: int
    code: str
    country: str
    flag: str
    score: int
    tier: str
    reason: str


class Question1(BaseModel):
    headline: str
    explanation: str


class Hurdle(BaseModel):
    category: str
    text: str


class Question2(BaseModel):
    summary: str
    whyLowerMatch: Optional[str] = None
    hurdles: List[Hurdle]
    disclaimer: str


class MatchResponse(BaseModel):
    groupKey: str
    label: str
    scoringVersion: str
    selected: dict
    showLowerMatchNotice: bool
    lowerMatchNotice: Optional[str] = None
    topThree: List[TopCountry]
    allCountries: List[AllCountryItem]
    question1: Question1
    question2: Question2


@router.post("/country-match", response_model=MatchResponse)
def country_match(req: MatchRequest):
    data = get_data()
    group, sel = data.get_country_result(req.dob, req.country)

    if group is None or sel is None:
        error_msg = sel if isinstance(sel, str) else "No data for this date/country"
        raise HTTPException(status_code=400, detail=error_msg)

    code = str(sel["countryCode"]).upper()
    score = int(sel["score"])
    rank = int(sel["rank"])
    tier = str(sel["tier"])
    is_top_three = rank <= 3

    profile = data.country_profile(code)
    country_name = profile["name"] if profile else code

    def ranked_entry(r: Dict) -> TopCountry:
        r_code = str(r["countryCode"]).upper()
        r_profile = data.country_profile(r_code) or {}
        r_score = int(r["score"])
        r_rank = int(r["rank"])
        return TopCountry(
            rank=r_rank,
            code=r_code,
            country=r_profile.get("name", r_code),
            score=r_score,
            tier=str(r.get("tier", "")),
            headline=r_profile.get("headline", ""),
            explanation=_fill_placeholders(
                str(r_profile.get("question1", {}).get("topMatch", "")), r_score, r_rank
            ),
        )

    top_three = [ranked_entry(r) for r in group["ranking"][:3]]

    all_countries = []
    for r in group["ranking"]:
        r_code = str(r["countryCode"]).upper()
        r_profile = data.country_profile(r_code) or {}
        r_score = int(r["score"])
        r_rank = int(r["rank"])
        all_countries.append(AllCountryItem(
            rank=r_rank,
            code=r_code,
            country=r_profile.get("name", r_code),
            flag=str(r_profile.get("flag", "")),
            score=r_score,
            tier=str(r.get("tier", "")),
            reason=r_profile.get("headline", ""),
        ))

    selected = {
        "code": code,
        "country": country_name,
        "score": score,
        "rank": rank,
        "tier": tier,
        "isTopThree": is_top_three,
    }

    show_lower = not is_top_three
    lower_notice = (
        f"{country_name} scores {score}% in this result, so it is not among "
        f"your three strongest matches. It is still worth researching."
    ) if show_lower else None

    q1_text = (
        profile["question1"].get("topMatch", "") if is_top_three
        else profile["question1"].get("otherMatch", "")
    )

    if is_top_three:
        q2_summary = profile["question2"].get("strongMatch", "")
        q2_why_lower = None
    else:
        q2_summary = f"{country_name} is not among your three strongest matches in this result."
        q2_why_lower = group["lowerMatchReason"] + " " + profile["question2"].get("lowerMatch", "")

    hurdles = [
        {"category": h.get("category", ""), "text": h.get("text", "")}
        for h in (profile["question2"].get("hurdles") or [])
    ]

    return MatchResponse(
        groupKey=group["groupKey"],
        label=group["label"],
        scoringVersion=group["scoringVersion"],
        selected=selected,
        showLowerMatchNotice=show_lower,
        lowerMatchNotice=lower_notice,
        topThree=top_three,
        allCountries=all_countries,
        question1=Question1(
            headline=profile.get("headline", ""),
            explanation=_fill_placeholders(q1_text, score, rank),
        ),
        question2=Question2(
            summary=q2_summary,
            whyLowerMatch=q2_why_lower,
            hurdles=[Hurdle(**h) for h in hurdles],
            disclaimer=profile["question2"].get("disclaimer", ""),
        ),
    )


@router.get("/countries")
def list_countries():
    data = get_data()
    return data.get_countries_list()


@router.get("/health")
def health_check():
    data = get_data()
    return data.health()
