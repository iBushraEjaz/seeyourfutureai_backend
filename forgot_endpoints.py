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
        return JSONResponse(status_code=500, content={
            "code": "firebase_error",
            "message": f"Could not check account: {exc}"})
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
