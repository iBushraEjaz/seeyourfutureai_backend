import json
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional

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
    _SERVICE_ACCOUNT = json.loads(_svc_json) if _svc_json else None
    FIREBASE_READY = bool(_SERVICE_ACCOUNT)
except Exception:
    _SERVICE_ACCOUNT = None
    FIREBASE_READY = False

IDENTITY_TOOLKIT = "https://identitytoolkit.googleapis.com/v1"
_SCOPE = "https://www.googleapis.com/auth/identitytoolkit"

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

ZODIAC_BUSINESSES: Dict[str, List[str]] = {
    "Aries": ["Fitness", "Sports", "Restaurants", "Construction"],
    "Taurus": ["Real estate", "Food", "Jewelry", "Agriculture"],
    "Gemini": ["Marketing", "Content", "Teaching", "Travel"],
    "Cancer": ["Catering", "Childcare", "Hospitality", "Home décor"],
    "Leo": ["Fashion", "Entertainment", "Events", "Media"],
    "Virgo": ["Accounting", "Healthcare", "Editing", "Consulting"],
    "Libra": ["Fashion and boutique", "Wedding and event planning", "Beauty and salon", "Interior design"],
    "Scorpio": ["Finance", "Psychology", "Investigation", "Research"],
    "Sagittarius": ["Travel", "Import-export", "Publishing", "Education"],
    "Capricorn": ["Manufacturing", "Banking", "Construction", "Consulting"],
    "Aquarius": ["Tech", "E-commerce", "Green energy", "Innovation"],
    "Pisces": ["Photography", "Art", "Wellness", "Music"],
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


class BusinessMatchRequest(BaseModel):
    name: str
    dob: str
    business: str


class BusinessMatchResponse(BaseModel):
    zodiac_sign: str
    business: str
    compatibility: int
    best_options: List[str]


@app.get("/")
def read_root():
    return {"message": "SeeYourFutureAI API is running"}


@app.get("/zodiac")
def get_zodiacs():
    return [{"sign": s, "dates": d} for s, d in ZODIAC_DATES.items()]


@app.get("/zodiac/{sign}")
def get_zodiac_businesses(sign: str):
    sign = sign.strip().title()
    if sign not in ZODIAC_BUSINESSES:
        raise HTTPException(status_code=404, detail=f"Unknown zodiac sign '{sign}'")
    return {"sign": sign, "dates": ZODIAC_DATES[sign], "businesses": ZODIAC_BUSINESSES[sign]}


@app.post("/match-business", response_model=BusinessMatchResponse)
def match_business(req: BusinessMatchRequest):
    sign = get_zodiac_sign(req.dob)
    if sign is None:
        raise HTTPException(status_code=400, detail="Invalid date of birth format. Use DD-MM-YYYY or YYYY-MM-DD")
    businesses = ZODIAC_BUSINESSES.get(sign, [])
    business_lower = req.business.strip().lower()
    matched = any(b.lower() == business_lower or business_lower in b.lower() for b in businesses)
    if matched:
        compat = 80 + (hash(req.business + sign) % 21)
    else:
        compat = 10 + (hash(req.business + sign) % 41)
    return BusinessMatchResponse(
        zodiac_sign=sign,
        business=req.business,
        compatibility=compat,
        best_options=businesses,
    )


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
        _firebase_delete_user(user["localId"])
        return {"message": "Orphaned account removed"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"message": f"Cleanup failed: {exc}"})
