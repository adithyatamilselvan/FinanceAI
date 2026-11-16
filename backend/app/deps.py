"""FastAPI dependencies: database handle and the authenticated user."""
from typing import Annotated

import jwt
from bson import ObjectId
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_db
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)
DB = Annotated[object, Depends(get_db)]


def current_user(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)], db=Depends(get_db)) -> dict:
    unauthorized = HTTPException(status_code=401, detail="Invalid or expired authentication", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_token(creds.credentials)
        uid = ObjectId(payload["sub"])
    except (jwt.PyJWTError, Exception):
        raise unauthorized
    user = db.users.find_one({"_id": uid})
    if not user or user.get("token_version", 0) != payload.get("tv", 0):
        raise unauthorized
    return user


CurrentUser = Annotated[dict, Depends(current_user)]
