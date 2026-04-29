from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from denzo.auth import check_credentials
from denzo.db import get_db

bp = Blueprint("auth", __name__)


def _load_user_plan(user_id: int) -> str:
    db = get_db()
    row = db.execute("SELECT plan FROM users WHERE id=?", (user_id,)).fetchone()
    db.close()
    return (row["plan"] or "free") if row else "free"


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard.index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_id = check_credentials(username, password)
        if user_id:
            session.clear()
            session["user_id"] = user_id
            session["username"] = username
            plan = _load_user_plan(user_id)
            session["plan"] = plan

            next_url = request.args.get("next", "")
            # Don't redirect back to landing or login
            if not next_url or next_url in ("/", "/login", "/landing", "/home"):
                next_url = None

            if not next_url:
                # Admin goes to Enterprise, Lite clients go to their dashboard
                db = get_db()
                role_row = db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
                role = role_row["role"] if role_row else "client"
                session["role"] = role
                if role == "admin":
                    next_url = url_for("dashboard.index")
                else:
                    # Find their tenant
                    client = db.execute(
                        "SELECT tenant_id FROM clients WHERE owner_user_id=? LIMIT 1",
                        (user_id,)
                    ).fetchone()
                    next_url = (
                        url_for("lite.dashboard", tenant_id=client["tenant_id"])
                        if client else url_for("dashboard.index")
                    )
                db.close()

            return redirect(next_url)
        else:
            error = "Invalid username or password."

    return render_template("auth/login.html", error=error)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
