"""Flask application factory and WSGI import target."""

import os
import warnings
from pathlib import Path

import click
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy.exc import IntegrityError

from .config import Config, INSTANCE_DIR

db = SQLAlchemy()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "admin.login"
login_manager.login_message = "Please sign in to access researcher tools."


def create_app(test_config=None):
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    application = Flask(__name__, instance_path=str(INSTANCE_DIR))
    application.config.from_object(Config)
    if test_config:
        application.config.update(test_config)
    if not os.environ.get("SECRET_KEY") and not application.config.get("TESTING"):
        warnings.warn("SECRET_KEY is unset; set a persistent secret before deployment.", RuntimeWarning)

    database_uri = application.config["SQLALCHEMY_DATABASE_URI"]
    if database_uri.startswith("sqlite:///") and not database_uri.startswith("sqlite:////"):
        relative_path = database_uri.removeprefix("sqlite:///")
        if relative_path != ":memory:":
            Path(application.root_path, relative_path).parent.mkdir(parents=True, exist_ok=True)

    db.init_app(application)
    csrf.init_app(application)
    login_manager.init_app(application)

    from . import models  # noqa: F401
    from .accounts import accounts_bp
    from .participant import participant_bp
    from .admin import admin_bp
    from .analysis import analysis_bp
    application.register_blueprint(participant_bp)
    application.register_blueprint(accounts_bp)
    application.register_blueprint(admin_bp)
    application.register_blueprint(analysis_bp)

    @login_manager.user_loader
    def load_researcher(user_id):
        from .models import Researcher, UserAccount

        account_type, separator, account_id = user_id.partition(":")
        try:
            if separator and account_type == "researcher":
                return db.session.get(Researcher, int(account_id))
            if separator and account_type == "user":
                return db.session.get(UserAccount, int(account_id))
            return db.session.get(Researcher, int(user_id))
        except ValueError:
            return None

    @application.context_processor
    def participant_context():
        from flask import current_app, session
        from flask_login import current_user
        from .models import Participant

        is_researcher = (
            current_user.is_authenticated
            and getattr(current_user, "account_type", None) == "researcher"
        )
        researcher_debug = bool(is_researcher and session.get("researcher_debug"))
        debug_participant = (
            db.session.get(Participant, session.get("participant_id"))
            if researcher_debug else None
        )

        return {
            "has_participant_session": bool(session.get("participant_id")),
            "experiment_test_mode": current_app.config.get("EXPERIMENT_TEST_MODE", False),
            "is_researcher": is_researcher,
            "researcher_debug": researcher_debug and bool(debug_participant and debug_participant.is_test),
            "debug_participant": debug_participant if debug_participant and debug_participant.is_test else None,
        }

    @application.cli.command("init-db")
    def init_db_command():
        """Create the database schema and optionally initialize the researcher account."""
        db.create_all()
        username = os.environ.get("ADMIN_USERNAME")
        password = os.environ.get("ADMIN_PASSWORD")
        if username and password:
            from .models import Researcher

            existing = Researcher.query.filter_by(username=username).first()
            if existing is None:
                researcher = Researcher(username=username)
                researcher.set_password(password)
                db.session.add(researcher)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    raise click.ClickException("Could not create researcher account.")
                click.echo(f"Created researcher account: {username}")
            else:
                click.echo(f"Researcher account already exists: {username}")
        click.echo("Database tables are ready.")

    @application.cli.command("seed-demo")
    def seed_demo_command():
        """Create a small, clearly marked synthetic dataset for dashboard checks."""
        from .demo_data import seed_demo_data

        created = seed_demo_data()
        click.echo(f"Created {created} synthetic participant records (excluded from research exports).")

    return application


app = create_app()