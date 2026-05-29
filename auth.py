from __future__ import annotations

from functools import wraps
from urllib.parse import urljoin, urlparse

from flask import (Blueprint, abort, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from models import User, db

auth_bp = Blueprint("auth", __name__)


# ── helpers ───────────────────────────────────────────────

def _safe_next(target: str | None, fallback: str = "/") -> str:
    """Return target URL only if it's on the same host (prevents open redirect)."""
    if not target:
        return fallback
    ref  = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    if test.scheme in ("http", "https") and ref.netloc == test.netloc:
        return target
    return fallback


def admin_required(f):
    """Decorator: requires an active login + admin role. 403 otherwise."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── routes ────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error   = None
    next_url = request.args.get("next", "")

    if request.method == "POST":
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.form.get("next",     "")

        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            error = "Invalid email or password."
        elif user.status == "pending":
            error = "Your request is still pending admin approval."
        elif user.status == "denied":
            error = "Your access request was not approved."
        else:
            login_user(user, remember=True)
            return redirect(_safe_next(next_url, url_for("index")))

    return render_template("login.html", error=error, next=next_url)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None

    if request.method == "POST":
        name    = request.form.get("name",    "").strip()
        email   = request.form.get("email",   "").strip().lower()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm",  "")

        if not all([name, email, password, confirm]):
            error = "All fields are required."
        elif password != confirm:
            error = "Passwords do not match."
        elif len(password) < 8 or not any(c.isupper() for c in password) or not any(c.isdigit() for c in password):
            error = "Password must be at least 8 characters, include one uppercase letter, and one number."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            user = User(name=name, email=email, role="viewer", status="pending")
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            return redirect(url_for("auth.pending"))

    return render_template("register.html", error=error)


@auth_bp.route("/pending")
def pending():
    return render_template("pending.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))