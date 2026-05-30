from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
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
    # CSRF HOOK: Add CSRF token validation before public internet exposure (Cloudflare/Funnel phase).
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify({"status": "error", "message": "User not found"}), 404
    user.status = "approved"
    db.session.commit()
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
    new_role = request.json.get("role", "") if request.is_json else request.form.get("role", "")
    if new_role not in ("viewer", "admin"):
        return jsonify({"status": "error", "message": f"Invalid role: {new_role}"}), 400
    user.role = new_role
    db.session.commit()
    return jsonify({"status": "success"})