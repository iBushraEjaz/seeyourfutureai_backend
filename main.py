import json
import os
import secrets
import smtplib
import time
import traceback
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
