from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user

from auth import admin_required
from models import User, db

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/users")
@admin_required
def users():
    pending  = User.query.filter_by(status="pending").order_by(User.created_at.asc()).all()
    approved = User.query.filter_by(status="approved").order_by(User.created_at.asc()).all()
    denied   = User.query.filter_by(status="denied").order_by(User.created_at.asc()).all()
    return render_template("admin/users.html",
                           pending=pending,
                           approved=approved,
                           denied=denied)


@admin_bp.route("/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve(user_id: int):
    """Approve a pending or re-approve a denied user."""
    user = User.query.get_or_404(user_id)
    user.status = "approved"
    db.session.commit()
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/deny", methods=["POST"])
@admin_required
def deny(user_id: int):
    """Deny a pending user's request."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        abort(400)   # can't deny yourself
    user.status = "denied"
    db.session.commit()
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/revoke", methods=["POST"])
@admin_required
def revoke(user_id: int):
    """Revoke an approved user's access."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        abort(400)   # can't revoke yourself
    user.status = "denied"
    db.session.commit()
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def change_role(user_id: int):
    """Promote or demote a user between viewer and admin."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        abort(400)   # can't change your own role
    new_role = request.form.get("role", "")
    if new_role not in ("viewer", "admin"):
        abort(400)
    user.role = new_role
    db.session.commit()
    return redirect(url_for("admin.users"))