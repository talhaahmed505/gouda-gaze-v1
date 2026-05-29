from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer,     primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name          = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)   # nullable — phase 2 Google users may not set one
    role          = db.Column(db.String(20),  nullable=False, default="viewer")   # "viewer" | "user" | "admin"
    status        = db.Column(db.String(20),  nullable=False, default="pending")  # "pending" | "approved" | "denied"
    google_sub    = db.Column(db.String(255), nullable=True, unique=True)         # phase 2: stable Google identity
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # ── password helpers ──────────────────────────────────

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    # ── convenience properties ────────────────────────────

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    # Flask-Login: only approved users are "active" (able to log in and hold sessions)
    @property
    def is_active(self) -> bool:
        return self.status == "approved"

    def __repr__(self) -> str:
        return f"<User {self.email} [{self.role}/{self.status}]>"