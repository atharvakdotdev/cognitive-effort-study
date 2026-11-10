"""Pseudonymous participant account registration and sign-in."""

import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from . import db
from .models import Participant, UserAccount

accounts_bp = Blueprint("accounts", __name__, url_prefix="/account")
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def _resume_account(account):
    participant = db.session.get(Participant, account.participant_id) if account.participant_id else None
    if account.participant_id and participant and participant.status not in ("withdrawn", "excluded"):
        session["participant_id"] = participant.participant_id
        session.permanent = True
    elif account.participant_id:
        account.participant_id = None
        session.pop("participant_id", None)
        db.session.commit()
    else:
        current_participant = db.session.get(Participant, session.get("participant_id")) if session.get("participant_id") else None
        if current_participant and current_participant.status not in ("withdrawn", "excluded"):
            account.participant_id = current_participant.participant_id
            db.session.commit()
    return redirect(url_for("participant.welcome"))


@accounts_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return _resume_account(current_user) if current_user.account_type == "user" else redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not USERNAME_PATTERN.fullmatch(username):
            flash("Use 3–32 letters, numbers, dots, dashes, or underscores for your username.", "error")
        elif len(password) < 12:
            flash("Use a password with at least 12 characters.", "error")
        elif UserAccount.query.filter_by(username=username).first():
            flash("That username is already in use.", "error")
        else:
            account = UserAccount(username=username)
            account.set_password(password)
            db.session.add(account)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That username is already in use.", "error")
            else:
                login_user(account)
                session.permanent = True
                return _resume_account(account)
    return render_template("account.html", mode="register", progress_stage="welcome")


@accounts_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return _resume_account(current_user) if current_user.account_type == "user" else redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        account = UserAccount.query.filter_by(username=username).first()
        if account and account.check_password(password):
            login_user(account)
            session.permanent = True
            return _resume_account(account)
        flash("The username or password was not recognised.", "error")
    return render_template("account.html", mode="login", progress_stage="welcome")


@accounts_bp.post("/logout")
def logout():
    if current_user.is_authenticated and current_user.account_type == "user":
        logout_user()
    return redirect(url_for("participant.welcome"))
