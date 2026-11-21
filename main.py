import json
import os
import secrets
import smtplib
import time
import traceback
import re
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name(".env"))
except Exception:
    pass

try:
    import jwt
    import requests
    _svc_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    
    if _svc_json:
        _SERVICE_ACCOUNT = json.loads(_svc_json)
        # THIS IS THE FIX: Converts literal "\n" into real newlines
        if _SERVICE_ACCOUNT and "private_key" in _SERVICE_ACCOUNT:
            _SERVICE_ACCOUNT["private_key"] = _SERVICE_ACCOUNT["private_key"].replace("\\n", "\n")
    else:
        _SERVICE_ACCOUNT = None
        
    FIREBASE_READY = bool(_SERVICE_ACCOUNT)
    if FIREBASE_READY:
        print(f"Firebase initialized - project_id: {_SERVICE_ACCOUNT.get('project_id')}, client_email: {_SERVICE_ACCOUNT.get('client_email')}, FIREBASE_READY: {FIREBASE_READY}")
    else:
        print(f"Firebase not ready - FIREBASE_READY: {FIREBASE_READY}")
except Exception as e:
    print(f"FIREBASE INIT ERROR: {e}")
    _SERVICE_ACCOUNT = None
    FIREBASE_READY = False

IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"
_SCOPE = "https://www.googleapis.com/auth/identitytoolkit https://www.googleapis.com/auth/datastore"

ACTIVE_OTPS = {}


def _firebase_access_token() -> str:
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": _SERVICE_ACCOUNT["client_email"],
            "scope": _SCOPE,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        _SERVICE_ACCOUNT["private_key"],
        algorithm="RS256",
    )
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _firebase_user_by_email(email: str) -> Optional[dict]:
    resp = requests.post(
        f"{IDENTITY_TOOLKIT}/projects/{_SERVICE_ACCOUNT['project_id']}/accounts:lookup",
        headers={"Authorization": f"Bearer {_firebase_access_token()}"},
        json={"email": [email]},
        timeout=20,
    )
    resp.raise_for_status()
    users = resp.json().get("users") or []
    return users[0] if users else None


def _firebase_update_password(local_id: str, password: str):
    resp = requests.post(
        f"{IDENTITY_TOOLKIT}/projects/{_SERVICE_ACCOUNT['project_id']}/accounts:update",
        headers={"Authorization": f"Bearer {_firebase_access_token()}"},
        json={"localId": local_id, "password": password},
        timeout=20,
    )
    resp.raise_for_status()


def _firebase_delete_user(local_id: str):
    resp = requests.post(
        f"{IDENTITY_TOOLKIT}/projects/{_SERVICE_ACCOUNT['project_id']}/accounts:delete",
        headers={"Authorization": f"Bearer {_firebase_access_token()}"},
        json={"localId": local_id},
        timeout=20,
    )
    resp.raise_for_status()


def _deletion_cooldown_expired(email: str) -> bool:
    resp = requests.get(
        f"https://firestore.googleapis.com/v1/projects/{_SERVICE_ACCOUNT['project_id']}"
        f"/databases/(default)/documents/deletedAccounts/{quote(email, safe='')}",
        headers={"Authorization": f"Bearer {_firebase_access_token()}"},
        timeout=20,
    )
    if resp.status_code != 200:
        return False  # no deletion record, so never delete
    ts = (resp.json().get("fields", {})
          .get("canRecreateAfter", {}).get("timestampValue"))
    if not ts:
        return False
    dt = datetime.fromisoformat(ts.split(".")[0].rstrip("Z") + "+00:00")
    return datetime.now(dt.tzinfo) >= dt


app = FastAPI(title="SeeYourFutureAI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ZODIAC_DATES: Dict[str, str] = {
    "Aries": "March 21 – April 19",
    "Taurus": "April 20 – May 20",
    "Gemini": "May 21 – June 20",
    "Cancer": "June 21 – July 22",
    "Leo": "July 23 – August 22",
    "Virgo": "August 23 – September 22",
    "Libra": "September 23 – October 22",
    "Scorpio": "October 23 – November 21",
    "Sagittarius": "November 22 – December 21",
    "Capricorn": "December 22 – January 19",
    "Aquarius": "January 20 – February 18",
    "Pisces": "February 19 – March 20",
}

# ------------------------------------------------------------
# BROAD BUSINESS CATEGORIES (keyword-based detection)
# ------------------------------------------------------------
BUSINESS_CATEGORIES: Dict[str, List[str]] = {
    "real_estate":    ["property", "real estate", "realty", "land", "housing",
                       "rental", "rent", "broker", "dealing", "plot", "apartment"],
    "arts":           ["painting", "art", "drawing", "illustration", "sculpture",
                       "craft", "sketch", "portrait", "canvas", "design"],
    "fitness":        ["gym", "fitness", "training", "trainer", "sports",
                       "coaching", "yoga", "athletic", "workout"],
    "food":           ["restaurant", "cafe", "catering", "food", "bakery",
                       "kitchen", "chef", "cook", "snack", "juice"],
    "construction":   ["construction", "builder", "building", "contractor",
                       "architecture", "civil", "renovation", "plumbing", "electric"],
    "technology":     ["tech", "software", "app", "web", "developer", "programming",
                       "it ", "saas", "ai", "data", "cyber", "startup"],
    "ecommerce":      ["ecommerce", "e-commerce", "online store", "shop", "dropship",
                       "retail", "store", "marketplace", "selling online"],
    "finance":        ["finance", "bank", "investment", "trading", "stocks",
                       "accounting", "audit", "tax", "insurance", "loan"],
    "healthcare":     ["health", "clinic", "medical", "doctor", "nurse", "pharmacy",
                       "therapy", "dental", "care", "wellness"],
    "education":      ["teaching", "tutor", "school", "education", "course",
                       "training center", "coaching class", "academy"],
    "marketing":      ["marketing", "advertising", "seo", "social media", "branding",
                       "content", "copywriting", "pr ", "influencer"],
    "media":          ["media", "video", "film", "photography", "youtube", "podcast",
                       "music", "editing", "animation", "streaming"],
    "travel":         ["travel", "tourism", "tour", "trip", "hotel", "hospitality",
                       "airbnb", "booking", "guide"],
       "fashion":        ["fashion", "clothing", "boutique", "tailor", "apparel",
                       "jewelry", "jewellery", "accessories", "beauty", "salon", "makeup", 
                       "parlour", "parlor"],
    "agriculture":    ["agriculture", "farm", "crop", "livestock", "dairy",
                       "poultry", "garden", "organic", "nursery"],
    "logistics":      ["logistics", "transport", "delivery", "courier", "shipping",
                       "warehouse", "supply chain", "trucking", "freight"],
    "consulting":     ["consulting", "consultant", "advisor", "strategy", "management",
                       "coaching business", "freelance consultant"],
    "events":         ["event", "wedding", "party", "planner", "decoration",
                       "catering event", "conference"],
    "publishing":     ["publishing", "book", "writing", "author", "blog",
                       "magazine", "newsletter", "print"],
    "manufacturing":  ["manufacturing", "factory", "production", "industrial",
                       "assembly", "fabrication", "workshop"],
    "energy":         ["energy", "solar", "green", "renewable", "environment",
                       "sustainability", "recycling"],
    "research":       ["research", "investigation", "analysis", "laboratory",
                       "science", "psychology", "survey"],
}

# ------------------------------------------------------------
# ZODIAC AFFINITY SCORES (0–10 per category)
# ------------------------------------------------------------
ZODIAC_AFFINITY: Dict[str, Dict[str, int]] = {
    "Aries":       {"fitness": 9, "sports": 9, "construction": 8, "food": 6,
                    "real_estate": 5, "events": 7, "logistics": 6, "arts": 5,
                    "technology": 4, "marketing": 5, "media": 5, "finance": 4},
    "Taurus":      {"real_estate": 9, "food": 9, "agriculture": 8, "fashion": 7,
                    "arts": 7, "finance": 6, "manufacturing": 6, "healthcare": 5,
                    "events": 5, "technology": 3, "travel": 5, "media": 4},
    "Gemini":      {"marketing": 9, "media": 8, "education": 8, "publishing": 7,
                    "travel": 7, "technology": 6, "ecommerce": 6, "events": 6,
                    "arts": 5, "finance": 4, "construction": 3, "food": 4},
    "Cancer":      {"food": 9, "healthcare": 8, "events": 7, "real_estate": 7,
                    "arts": 6, "education": 6, "fashion": 5, "travel": 5,
                    "technology": 3, "finance": 4, "logistics": 4, "media": 5},
    "Leo":         {"media": 9, "events": 9, "fashion": 8, "arts": 8,
                    "marketing": 7, "education": 6, "travel": 6, "food": 6,
                    "real_estate": 5, "technology": 4, "finance": 4, "healthcare": 4},
    "Virgo":       {"healthcare": 9, "consulting": 8, "finance": 8, "education": 7,
                    "research": 7, "technology": 6, "agriculture": 6, "publishing": 6,
                    "food": 5, "arts": 4, "media": 4, "events": 4},
    "Libra":       {"fashion": 9, "events": 9, "arts": 8, "real_estate": 7,
                    "media": 7, "marketing": 6, "healthcare": 5, "education": 5,
                    "technology": 4, "finance": 5, "food": 5, "travel": 6},
    "Scorpio":     {"finance": 9, "research": 8, "healthcare": 7, "technology": 7,
                    "real_estate": 6, "consulting": 6, "media": 6, "arts": 5,
                    "events": 4, "food": 4, "fashion": 5, "marketing": 5},
    "Sagittarius": {"travel": 9, "education": 8, "publishing": 8, "media": 7,
                    "logistics": 7, "events": 6, "marketing": 6, "technology": 5,
                    "real_estate": 5, "arts": 5, "finance": 4, "food": 5},
    "Capricorn":   {"manufacturing": 9, "finance": 9, "construction": 8, "consulting": 8,
                    "real_estate": 7, "logistics": 7, "technology": 6, "healthcare": 5,
                    "education": 5, "media": 4, "arts": 4, "food": 4},
    "Aquarius":    {"technology": 9, "ecommerce": 8, "energy": 8, "research": 7,
                    "media": 7, "marketing": 6, "education": 6, "finance": 5,
                    "arts": 5, "events": 5, "healthcare": 5, "construction": 4},
    "Pisces":      {"arts": 9, "media": 9, "healthcare": 7, "travel": 7,
                    "events": 6, "education": 6, "food": 6, "fashion": 6,
                    "technology": 4, "finance": 3, "construction": 3, "logistics": 4},
}

MONTH_NUMBERS: Dict[str, int] = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}


def get_zodiac_sign(dob: str) -> Optional[str]:
    try:
        dob = dob.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(dob, fmt)
                break
            except ValueError:
                continue
        else:
            return None
        month, day = dt.month, dt.day
        for sign, date_range in ZODIAC_DATES.items():
            start_str, end_str = date_range.split(" – ")
            start_month_name, start_day = start_str.strip().split(" ")
            end_month_name, end_day = end_str.strip().split(" ")
            start_month = MONTH_NUMBERS[start_month_name]
            end_month = MONTH_NUMBERS[end_month_name]
            start_day = int(start_day)
            end_day = int(end_day)
            if (month == start_month and day >= start_day) or (month == end_month and day <= end_day):
                return sign
            if start_month > end_month:
                if (month >= start_month and day >= start_day) or (month <= end_month and day <= end_day):
                    return sign
        return None
    except Exception:
        return None


# ------------------------------------------------------------
# CATEGORY DETECTION + SCORING HELPERS
# ------------------------------------------------------------
def detect_category(business: str) -> Optional[str]:
    """Return the best-matching category for a free-text business description."""
    if not business:
        return None
    b = " " + business.strip().lower() + " "
    b = re.sub(r"\s+", " ", b)
    best_cat, best_hits = None, 0
    for cat, keywords in BUSINESS_CATEGORIES.items():
        hits = sum(1 for kw in keywords if kw in b)
        if hits > best_hits:
            best_cat, best_hits = cat, hits
    return best_cat  # None if nothing matched


def _score_for(sign: str, category: Optional[str]) -> int:
    """0–100 compatibility score based on the sign's affinity table."""
    if category is None:
        return 40  # unknown category → neutral-ish
    affinity = ZODIAC_AFFINITY.get(sign, {})
    raw = affinity.get(category)
    if raw is None:
        return 45  # category not scored for this sign → mild neutral
    # affinity is 0–10 → map to 30–100 so nothing is absurdly low
    return int(round(30 + (raw / 10) * 70))


def _top_options(sign: str, n: int = 5) -> List[str]:
    """The sign's strongest categories, highest first."""
    affinity = ZODIAC_AFFINITY.get(sign, {})
    ranked = sorted(affinity.items(), key=lambda kv: kv[1], reverse=True)
    return [cat.replace("_", " ") for cat, _ in ranked[:n]]


def _sign_note(sign: str, category: Optional[str], score: int) -> str:
    if category is None:
        return ("We couldn't map that business to a known category. "
                "Try a more specific description (e.g. 'property dealing', "
                "'painting', 'online clothing store').")
    cat_label = category.replace("_", " ")
    if score >= 80:
        strength = "a strong traditional fit"
    elif score >= 60:
        strength = "a moderate fit"
    else:
        strength = "a weaker traditional fit — not a bad choice, just less aligned"
    return (f"For {sign}, {cat_label} is {strength} according to the zodiac "
            f"affinity table. Real-world fit still depends on your skills, "
            f"capital, and market demand.")


@app.get("/")
def read_root():
    return {"message": "SeeYourFutureAI API is running"}


@app.get("/zodiac")
def get_zodiacs():
    return [{"sign": s, "dates": d} for s, d in ZODIAC_DATES.items()]


@app.get("/zodiac/{sign}")
def get_zodiac_businesses(sign: str):
    sign = sign.strip().title()
    if sign not in ZODIAC_AFFINITY:
        raise HTTPException(status_code=404, detail=f"Unknown zodiac sign '{sign}'")
    return {
        "sign": sign,
        "dates": ZODIAC_DATES[sign],
        "top_businesses": _top_options(sign),
    }


@app.get("/categories")
def list_categories():
    return {
        "categories": [
            {"name": cat, "keywords": kws}
            for cat, kws in BUSINESS_CATEGORIES.items()
        ]
    }


@app.get("/zodiac/{sign}/affinity")
def zodiac_affinity(sign: str):
    sign = sign.strip().title()
    if sign not in ZODIAC_AFFINITY:
        raise HTTPException(status_code=404, detail=f"Unknown zodiac sign '{sign}'")
    ranked = sorted(ZODIAC_AFFINITY[sign].items(), key=lambda kv: kv[1], reverse=True)
    return {
        "sign": sign,
        "dates": ZODIAC_DATES[sign],
        "ranked_categories": [
            {"category": c.replace("_", " "), "affinity": v} for c, v in ranked
        ],
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "firebase_ready": FIREBASE_READY}


def _otp_mail_html(otp: str, kind: str) -> str:
    purpose = "Reset Password" if kind == "reset" else "Verification"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5faf9; margin: 0; padding: 40px 20px; }}
            .container {{ max-width: 500px; margin: 0 auto; background-color: #ffffff; border-radius: 20px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.05); border: 1px solid #e0e8e6; }}
            .header {{ background-color: #075B50; padding: 30px; text-align: center; }}
            .header h1 {{ color: #ffffff; margin: 0; font-size: 24px; font-weight: 800; letter-spacing: -0.5px; }}
            .content {{ padding: 40px 30px; text-align: center; }}
            .content p {{ color: #647B79; font-size: 15px; line-height: 1.6; margin: 0 0 20px 0; }}
            .otp-box {{ background-color: #F9F9F9; border: 1.5px dashed #E0E8E6; border-radius: 12px; padding: 20px; margin: 30px 0; }}
            .otp {{ font-size: 42px; font-weight: 800; color: #075B50; letter-spacing: 8px; margin: 0; }}
            .expiry {{ color: #E5B800; font-size: 13px; font-weight: 600; background-color: #FFF9E6; padding: 8px 16px; border-radius: 20px; display: inline-block; }}
            .footer {{ background-color: #f5faf9; padding: 20px; text-align: center; color: #9AA9A7; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>See Your Future AI</h1>
            </div>
            <div class="content">
                <p>You requested a <strong>{purpose}</strong> code.</p>
                <div class="otp-box">
                    <p class="otp">{otp}</p>
                </div>
                <div class="expiry">Expires in 1 minute</div>
                <p style="margin-top: 20px; font-size: 12px;">If you did not request this, please ignore this email.</p>
            </div>
            <div class="footer">
                &copy; 2024 See Your Future AI. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """


@app.get("/send-otp")
def send_otp(email: str, otp: str, type: str = "signup"):
    sender = os.getenv("SMTP_EMAIL")
    password = os.getenv("SMTP_PASSWORD")
    if not sender or not password:
        return JSONResponse(
            status_code=500,
            content={"message": "Email service is not configured on the server"},
        )

    ACTIVE_OTPS[email] = {"otp": otp, "expires_at": time.time() + 60}

    msg = EmailMessage()
    msg["Subject"] = f"SeeYourFutureAI {type.capitalize()} Code"
    msg["From"] = sender
    msg["To"] = email

    msg.set_content(_otp_mail_html(otp, type), subtype="html")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(sender, password.replace(" ", ""))
            server.send_message(msg)
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"message": f"Failed to send OTP email: {exc}"}
        )

    return {"message": "OTP sent", "email": email}

class VerifyOtpRequest(BaseModel):
    email: str
    otp: str

@app.post("/verify-otp")
def verify_otp(req: VerifyOtpRequest):
    record = ACTIVE_OTPS.get(req.email)
    if not record:
        return JSONResponse(status_code=400, content={"message": "No active OTP found"})
    if time.time() > record["expires_at"]:
        del ACTIVE_OTPS[req.email]
        return JSONResponse(status_code=400, content={"message": "OTP expired"})
    if record["otp"] != req.otp:
        return JSONResponse(status_code=400, content={"message": "Invalid OTP"})
    del ACTIVE_OTPS[req.email]
    return {"message": "OTP verified"}



@app.get("/check-email")
def check_email(email: str):
    if not FIREBASE_READY:
        return JSONResponse(
            status_code=503,
            content={"message": "Firebase admin is not configured on the server"},
        )
    try:
        return {"registered": _firebase_user_by_email(email) is not None}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": str(exc)})


class ResetPasswordRequest(BaseModel):
    email: str
    newPassword: str


@app.post("/reset-password")
def reset_password(req: ResetPasswordRequest):
    if not FIREBASE_READY:
        return JSONResponse(
            status_code=503,
            content={"message": "Firebase admin is not configured on the server"},
        )
    if len(req.newPassword) < 6:
        return JSONResponse(
            status_code=400, content={"message": "Password must be at least 6 characters"}
        )
    try:
        user = _firebase_user_by_email(req.email)
        if user is None:
            return JSONResponse(
                status_code=404, content={"message": "No account found for this email"}
            )
        _firebase_update_password(user["localId"], req.newPassword)
        return {"message": "Password updated"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Password reset failed: {exc}"})


@app.post("/cleanup-orphaned")
def cleanup_orphaned(email: str):
    if not FIREBASE_READY:
        return JSONResponse(
            status_code=503,
            content={"message": "Firebase admin is not configured on the server"},
        )
    try:
        user = _firebase_user_by_email(email)
        if user is None:
            return {"message": "Nothing to clean up"}
        if not _deletion_cooldown_expired(email.strip().lower()):
            return JSONResponse(status_code=403, content={"message": "Not eligible for cleanup"})
        _firebase_delete_user(user["localId"])
        return {"message": "Orphaned account removed"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Cleanup failed: {exc}"})
# ---------- FORGOT PASSWORD (separate from signup) ----------
RESET_OTP_TTL = 60
RESET_TOKEN_TTL = 600
RESET_MAX_ATTEMPTS = 5

RESET_OTPS = {}
RESET_TOKENS = {}


def _send_mail(to_email, subject, html):
    sender = os.getenv("SMTP_EMAIL")
    password = os.getenv("SMTP_PASSWORD")
    if not sender or not password:
        raise RuntimeError("Email service is not configured on the server")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
        server.login(sender, password.replace(" ", ""))
        server.send_message(msg)


class ForgotSendOtpRequest(BaseModel):
    email: str


class ForgotVerifyOtpRequest(BaseModel):
    email: str
    otp: str


class ForgotResetRequest(BaseModel):
    email: str
    reset_token: str
    new_password: str


@app.post("/forgot-password/send-otp")
def forgot_send_otp(req: ForgotSendOtpRequest):
    email = req.email.strip().lower()

    if not FIREBASE_READY:
        return JSONResponse(status_code=503, content={
            "code": "firebase_not_configured",
            "message": "Firebase admin is not configured on the server"})

    try:
        user = _firebase_user_by_email(email)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "code": "firebase_error",
            "message": "Could not process request. Try again."})
    if user is None:
        return JSONResponse(status_code=404, content={
            "code": "not_registered",
            "message": "Email not registered"})

    otp = f"{secrets.randbelow(10000):04d}"
    RESET_OTPS[email] = {"otp": otp, "expires_at": time.time() + RESET_OTP_TTL, "attempts": 0}

    try:
        _send_mail(email, "SeeYourFutureAI Reset Password Code", _otp_mail_html(otp, "reset"))
    except Exception as exc:
        RESET_OTPS.pop(email, None)
        return JSONResponse(status_code=500, content={
            "code": "mail_error",
            "message": f"Failed to send OTP email: {exc}"})

    return {"message": "OTP sent", "email": email}


@app.post("/forgot-password/verify-otp")
def forgot_verify_otp(req: ForgotVerifyOtpRequest):
    email = req.email.strip().lower()
    rec = RESET_OTPS.get(email)
    if not rec:
        return JSONResponse(status_code=400, content={"message": "No active OTP found"})
    if time.time() > rec["expires_at"]:
        RESET_OTPS.pop(email, None)
        return JSONResponse(status_code=400, content={"message": "OTP expired"})
    if rec["attempts"] >= RESET_MAX_ATTEMPTS:
        RESET_OTPS.pop(email, None)
        return JSONResponse(status_code=429, content={"message": "Too many attempts. Request a new OTP"})
    if not secrets.compare_digest(rec["otp"], req.otp.strip()):
        rec["attempts"] += 1
        return JSONResponse(status_code=400, content={"message": "Invalid OTP"})

    RESET_OTPS.pop(email, None)
    token = secrets.token_urlsafe(32)
    RESET_TOKENS[token] = {"email": email, "expires_at": time.time() + RESET_TOKEN_TTL}
    return {"message": "OTP verified", "reset_token": token}


@app.post("/forgot-password/reset")
def forgot_reset(req: ForgotResetRequest):
    email = req.email.strip().lower()
    rec = RESET_TOKENS.get(req.reset_token)
    if not rec or rec["email"] != email or time.time() > rec["expires_at"]:
        return JSONResponse(status_code=400, content={"message": "Reset session expired. Start again"})
    if len(req.new_password) < 6:
        return JSONResponse(status_code=400, content={"message": "Password must be at least 6 characters"})
    if not FIREBASE_READY:
        return JSONResponse(status_code=503, content={"message": "Firebase admin is not configured on the server"})
    try:
        user = _firebase_user_by_email(email)
        if user is None:
            return JSONResponse(status_code=404, content={"message": "Email not registered"})
        _firebase_update_password(user["localId"], req.new_password)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Password reset failed: {exc}"})
    RESET_TOKENS.pop(req.reset_token, None)
    return {"message": "Password updated"}


# ------------------------------------------------------------
# Business compatibility + growth plan routers
# ------------------------------------------------------------
from business_result import router as business_result_router
from business_plan_growth import router as business_plan_growth_router
from country_match import router as country_match_router
from visa_endpoints import router as visa_router

app.include_router(business_result_router)
app.include_router(business_plan_growth_router)
app.include_router(country_match_router)
app.include_router(visa_router)