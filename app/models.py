"""Persistent research, participant, and researcher-account models."""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


def utc_now():
    return datetime.now(timezone.utc)


class Researcher(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    account_type = "researcher"

    def get_id(self):
        return f"researcher:{self.id}"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class UserAccount(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(32), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"))
    account_type = "user"

    def get_id(self):
        return f"user:{self.id}"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Participant(db.Model):
    participant_id = db.Column(db.String(32), primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime(timezone=True))
    consented = db.Column(db.Boolean, nullable=False, default=False)
    age_eligible = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(24), nullable=False, default="in_progress")
    current_stage = db.Column(db.String(32), nullable=False, default="consent")
    exclusion_reason = db.Column(db.String(120))
    random_seed = db.Column(db.Integer, nullable=False)
    is_test = db.Column(db.Boolean, nullable=False, default=False, index=True)
    quiz_attempts = db.Column(db.Integer, nullable=False, default=0)
    practice_order = db.Column(db.Text, nullable=False, default="[]")
    adaptive_state = db.Column(db.Text, nullable=False, default="{}")
    quality_flags = db.Column(db.Text, nullable=False, default="[]")

    choices = db.relationship("Choice", backref="participant", lazy=True, cascade="all, delete-orphan")
    task_runs = db.relationship("NBackTask", backref="participant", lazy=True, cascade="all, delete-orphan")
    ratings = db.relationship("EffortRating", backref="participant", lazy=True, cascade="all, delete-orphan")


class Choice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    trial_number = db.Column(db.Integer, nullable=False)
    condition_id = db.Column(db.String(24), nullable=False)
    n_back_level = db.Column(db.Integer)
    duration_minutes = db.Column(db.Integer)
    alternative_reward = db.Column(db.Float)
    task_reward = db.Column(db.Float, nullable=False)
    chosen_option = db.Column(db.String(16))
    task_selected = db.Column(db.Boolean)
    no_task_position = db.Column(db.String(8), nullable=False)
    task_position = db.Column(db.String(8), nullable=False)
    is_catch = db.Column(db.Boolean, nullable=False, default=False)
    catch_type = db.Column(db.String(16))
    timestamp = db.Column(db.DateTime(timezone=True))
    response_time_ms = db.Column(db.Float)
    __table_args__ = (db.UniqueConstraint("participant_id", "trial_number", name="uq_choice_participant_trial"),)


class NBackTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    task_level = db.Column(db.Integer, nullable=False)
    run_type = db.Column(db.String(16), nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at = db.Column(db.DateTime(timezone=True))
    actual_duration_seconds = db.Column(db.Float)
    sequence_json = db.Column(db.Text, nullable=False, default="[]")
    __table_args__ = (db.UniqueConstraint("participant_id", "task_level", "run_type", name="uq_task_run"),)

    responses = db.relationship("NBackResponse", backref="task_run", lazy=True, cascade="all, delete-orphan")


class NBackResponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_run_id = db.Column(db.Integer, db.ForeignKey("n_back_task.id"), nullable=False, index=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    task_level = db.Column(db.Integer, nullable=False)
    trial_index = db.Column(db.Integer, nullable=False)
    stimulus = db.Column(db.String(1), nullable=False)
    correct_response = db.Column(db.Boolean, nullable=False)
    actual_response = db.Column(db.String(16))
    accuracy = db.Column(db.Boolean, nullable=False)
    reaction_time_ms = db.Column(db.Float)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    __table_args__ = (db.UniqueConstraint("task_run_id", "trial_index", name="uq_nback_task_trial"),)


class EffortRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    n_back_level = db.Column(db.Integer, nullable=False)
    rating_type = db.Column(db.String(16), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    task_run_id = db.Column(db.Integer, db.ForeignKey("n_back_task.id"))
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    __table_args__ = (db.UniqueConstraint("participant_id", "n_back_level", "rating_type", name="uq_effort_rating"),)


class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    attempt_number = db.Column(db.Integer, nullable=False)
    answers_json = db.Column(db.Text, nullable=False)
    passed = db.Column(db.Boolean, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    __table_args__ = (db.UniqueConstraint("participant_id", "attempt_number", name="uq_quiz_attempt"),)


class BrowserEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, index=True)
    event_type = db.Column(db.String(24), nullable=False)
    stage = db.Column(db.String(24), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class ImplementedOutcome(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), nullable=False, unique=True, index=True)
    choice_id = db.Column(db.Integer, db.ForeignKey("choice.id"), nullable=False, unique=True)
    chosen_option = db.Column(db.String(16), nullable=False)
    task_selected = db.Column(db.Boolean, nullable=False)
    credits_awarded = db.Column(db.Float, nullable=False)
    task_completed = db.Column(db.Boolean, nullable=False, default=False)
    selected_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    task_completed_at = db.Column(db.DateTime(timezone=True))

    choice = db.relationship("Choice")


class DebriefResponse(db.Model):
    participant_id = db.Column(db.String(32), db.ForeignKey("participant.participant_id"), primary_key=True)
    open_feedback = db.Column(db.Text)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)