from flask import session, redirect, url_for, request, jsonify
from functools import wraps
from werkzeug.security import check_password_hash
from denzo.db import get_db


def can_access_tenant(tenant_id: str) -> bool:
    """Admins can access any tenant; client users only their own."""
    if "user_id" not in session:
        return False
    if session.get("role") == "admin":
        return True
    db = get_db()
    row = db.execute(
        "SELECT id FROM clients WHERE tenant_id=? AND owner_user_id=?",
        (tenant_id, session["user_id"])
    ).fetchone()
    db.close()
    return row is not None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def check_credentials(username: str, password: str):
    db = get_db()
    user = db.execute(
        "SELECT id, password_hash FROM users WHERE username=?", (username,)
    ).fetchone()
    db.close()
    if user and check_password_hash(user["password_hash"], password):
        return user["id"]
    return None
