from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from auth import admin_required
from models import User, db

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
auth_log = logging.getLogger("auth")


# ── Views ──────────────────────────────────────────────────────────────────────

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


# ── API endpoints ──────────────────────────────────────────────────────────────

@admin_bp.route("/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve(user_id: int):
    # CSRF HOOK: Add CSRF token validation before public internet exposure (Cloudflare/Funnel phase).
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"status": "error", "message": "User not found"}), 404
    prev_status = user.status
    user.status = "approved"
    db.session.commit()
    auth_log.info(
        f"ADMIN_APPROVE | admin={current_user.email}"
        f" | target={user.email} | prev_status={prev_status}"
    )
    return jsonify({"status": "success"})


@admin_bp.route("/users/<int:user_id>/deny", methods=["POST"])
@admin_required
def deny(user_id: int):
    # CSRF HOOK: Add CSRF token validation before public internet exposure.
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"status": "error", "message": "User not found"}), 404
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "Cannot deny yourself"}), 400
    user.status = "denied"
    db.session.commit()
    auth_log.info(
        f"ADMIN_DENY | admin={current_user.email} | target={user.email}"
    )
    return jsonify({"status": "success"})


@admin_bp.route("/users/<int:user_id>/revoke", methods=["POST"])
@admin_required
def revoke(user_id: int):
    # CSRF HOOK: Add CSRF token validation before public internet exposure.
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"status": "error", "message": "User not found"}), 404
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "Cannot revoke yourself"}), 400
    user.status = "denied"
    db.session.commit()
    auth_log.info(
        f"ADMIN_REVOKE | admin={current_user.email} | target={user.email}"
    )
    return jsonify({"status": "success"})


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def change_role(user_id: int):
    # CSRF HOOK: Add CSRF token validation before public internet exposure.
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"status": "error", "message": "User not found"}), 404
    if user.id == current_user.id:
        return jsonify({"status": "error", "message": "Cannot change your own role"}), 400
    # Require JSON — form submission path removed to prevent CSRF form-based attacks
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415
    new_role = request.json.get("role", "")
    if new_role not in ("viewer", "admin"):
        return jsonify({"status": "error", "message": f"Invalid role: {new_role}"}), 400
    old_role = user.role
    user.role = new_role
    db.session.commit()
    auth_log.info(
        f"ADMIN_ROLE_CHANGE | admin={current_user.email}"
        f" | target={user.email} | {old_role} -> {new_role}"
    )
    return jsonify({"status": "success"})