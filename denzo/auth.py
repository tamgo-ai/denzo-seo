from flask import session, redirect, url_for, request
from functools import wraps
from werkzeug.security import check_password_hash
from denzo.db import get_db


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
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
