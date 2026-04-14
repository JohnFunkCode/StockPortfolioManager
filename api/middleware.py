"""
Authentication and authorisation middleware.

@require_auth  — validates the JWT session cookie; populates flask.g.user
@require_role  — checks g.user["role"] against an allowed set (must follow @require_auth)
"""
from functools import wraps

import jwt
from flask import g, jsonify, request

from db.config import get_secret


def _jwt_secret() -> str:
    return get_secret("jwt-secret")


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.cookies.get("session")
        if not token:
            return jsonify({"error": "Unauthorized", "status": 401}), 401
        try:
            g.user = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Session expired", "status": 401}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token", "status": 401}), 401
        return f(*args, **kwargs)
    return wrapper


def require_role(*roles: str):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not hasattr(g, "user"):
                return jsonify({"error": "Unauthorized", "status": 401}), 401
            if g.user.get("role") not in roles:
                return jsonify({"error": "Forbidden", "status": 403}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator
