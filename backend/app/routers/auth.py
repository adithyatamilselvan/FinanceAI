from fastapi import APIRouter, Depends, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from ..config import get_settings
from ..db import get_db
from ..deps import CurrentUser
from ..schemas import LoginIn, PasswordChange, ProfileUpdate, RegisterIn
from ..security import auth_limiter, create_access_token, hash_password, verify_password
from ..util import clean_text, now_utc

router = APIRouter(tags=["auth"])


def user_out(u: dict) -> dict:
    return {
        "id": str(u["_id"]), "email": u["email"], "name": u["name"], "currency": u.get("currency", "INR"),
        "monthly_income": u.get("monthly_income", 0), "current_savings": u.get("current_savings"),
        "savings_target_pct": u.get("savings_target_pct"), "created_at": u["created_at"].isoformat(),
    }


def _limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    if not auth_limiter.allow(f"auth:{ip}", get_settings().auth_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute and try again.")


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn, request: Request, db=Depends(get_db)):
    _limit(request)
    doc = {
        "email": body.email.lower(), "name": clean_text(body.name, 80), "password_hash": hash_password(body.password),
        "currency": "INR", "monthly_income": 0, "current_savings": None, "savings_target_pct": None,
        "token_version": 0, "created_at": now_utc(),
    }
    try:
        res = db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    doc["_id"] = res.inserted_id
    token, ttl = create_access_token(str(res.inserted_id), 0)
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "user": user_out(doc)}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db=Depends(get_db)):
    _limit(request)
    # Per-account throttle that does not depend on the (spoofable) client IP: slows password guessing on one account.
    if not auth_limiter.allow(f"acct:{body.email.lower()}", get_settings().auth_rate_limit_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a minute and try again.")
    user = db.users.find_one({"email": body.email.lower()})
    ok = verify_password(body.password, user["password_hash"] if user else None)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token, ttl = create_access_token(str(user["_id"]), user.get("token_version", 0))
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl, "user": user_out(user)}


@router.post("/auth/logout", status_code=204)
def logout(user: CurrentUser, db=Depends(get_db)):
    """Invalidates every token issued so far for this user (server-side token version bump)."""
    db.users.update_one({"_id": user["_id"]}, {"$inc": {"token_version": 1}})


@router.get("/profile")
def get_profile(user: CurrentUser):
    return user_out(user)


@router.patch("/profile")
def update_profile(body: ProfileUpdate, user: CurrentUser, db=Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = clean_text(changes["name"], 80)
    if changes:
        db.users.update_one({"_id": user["_id"]}, {"$set": changes})
    return user_out(db.users.find_one({"_id": user["_id"]}))


@router.post("/profile/password", status_code=204)
def change_password(body: PasswordChange, user: CurrentUser, db=Depends(get_db)):
    if not verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    db.users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": hash_password(body.new_password)}, "$inc": {"token_version": 1}})
