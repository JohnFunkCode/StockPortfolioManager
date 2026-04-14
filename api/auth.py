"""
OAuth 2.0 authentication blueprint.

Routes:
  GET  /auth/login     — redirect to Google sign-in
  GET  /auth/callback  — handle OAuth callback, issue session JWT
  POST /auth/refresh   — issue a new JWT (accepts an expired token)
  POST /auth/logout    — clear session cookie
  GET  /auth/me        — return current user info from JWT
"""
import os
import secrets
import time
from urllib.parse import urlencode

import jwt
import requests as http_requests
from flask import Blueprint, g, jsonify, make_response, redirect, request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import text

from db.config import get_secret
from db.database import get_db

auth_bp = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redirect_uri() -> str:
    return os.environ.get("OAUTH_REDIRECT_URI", "http://localhost:5001/auth/callback")


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:5173")


def _build_google_auth_url(state: str) -> str:
    params = {
        "client_id":     get_secret("google-oauth-client-id"),
        "redirect_uri":  _redirect_uri(),
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "offline",
        "prompt":        "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def _exchange_code(code: str) -> dict:
    resp = http_requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code":          code,
            "client_id":     get_secret("google-oauth-client-id"),
            "client_secret": get_secret("google-oauth-client-secret"),
            "redirect_uri":  _redirect_uri(),
            "grant_type":    "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _verify_google_id_token(token_str: str) -> dict:
    return id_token.verify_oauth2_token(
        token_str,
        google_requests.Request(),
        get_secret("google-oauth-client-id"),
    )


def _issue_jwt(user_row) -> str:
    payload = {
        "sub":       str(user_row["id"]),
        "tenant_id": str(user_row["tenant_id"]),
        "email":     user_row["email"],
        "role":      user_row["role"],
        "iat":       int(time.time()),
        "exp":       int(time.time()) + 3600,
    }
    return jwt.encode(payload, get_secret("jwt-secret"), algorithm="HS256")


def _session_cookie(response, token: str):
    """Attach the JWT session cookie to a response."""
    secure = os.environ.get("FLASK_ENV") == "production"
    response.set_cookie(
        "session", token,
        httponly=True,
        secure=secure,
        samesite="Lax",
        max_age=3600,
    )
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/auth/login")
def login():
    state = secrets.token_urlsafe(32)
    resp = make_response(redirect(_build_google_auth_url(state)))
    secure = os.environ.get("FLASK_ENV") == "production"
    resp.set_cookie(
        "oauth_state", state,
        httponly=True, secure=secure, samesite="Lax", max_age=300,
    )
    return resp


@auth_bp.route("/auth/callback")
def callback():
    if error := request.args.get("error"):
        return jsonify({"error": error}), 400

    state = request.args.get("state")
    if not state or state != request.cookies.get("oauth_state"):
        return jsonify({"error": "Invalid state parameter — possible CSRF"}), 400

    code = request.args.get("code")
    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    try:
        tokens = _exchange_code(code)
        claims = _verify_google_id_token(tokens["id_token"])
    except Exception as exc:
        return jsonify({"error": f"Token exchange failed: {exc}"}), 400

    google_sub = claims["sub"]

    with get_db() as conn:
        row = conn.execute(
            text("SELECT id, tenant_id, email, role FROM users WHERE google_sub = :sub"),
            {"sub": google_sub},
        ).mappings().fetchone()

    if not row:
        return jsonify({
            "error": "Account not found. Contact an admin to be invited."
        }), 403

    resp = make_response(redirect(f"{_frontend_url()}/dashboard"))
    _session_cookie(resp, _issue_jwt(row))
    resp.delete_cookie("oauth_state")
    return resp


@auth_bp.route("/auth/refresh", methods=["POST"])
def refresh():
    token = request.cookies.get("session")
    if not token:
        return jsonify({"error": "No session cookie"}), 401

    try:
        payload = jwt.decode(
            token,
            get_secret("jwt-secret"),
            algorithms=["HS256"],
            options={"verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid token"}), 401

    with get_db() as conn:
        row = conn.execute(
            text("SELECT id, tenant_id, email, role FROM users WHERE id = :id"),
            {"id": payload["sub"]},
        ).mappings().fetchone()

    if not row:
        return jsonify({"error": "User not found"}), 403

    resp = make_response(jsonify({"ok": True}))
    _session_cookie(resp, _issue_jwt(row))
    return resp


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("session")
    return resp


@auth_bp.route("/auth/me")
def me():
    token = request.cookies.get("session")
    if not token:
        return jsonify({"error": "Not authenticated"}), 401
    try:
        payload = jwt.decode(token, get_secret("jwt-secret"), algorithms=["HS256"])
        return jsonify({
            "sub":       payload["sub"],
            "email":     payload["email"],
            "role":      payload["role"],
            "tenant_id": payload["tenant_id"],
        })
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Session expired"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid token"}), 401
