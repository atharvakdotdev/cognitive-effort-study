"""Authenticated researcher dashboard and research-only CSV exports."""

import csv
import io
import json
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_user, logout_user

from . import db
from . import experiment_design as design
from .auth import researcher_required
from .models import (
    BrowserEvent,
    Choice,
    DebriefResponse,
    EffortRating,
    ImplementedOutcome,
    NBackResponse,
    NBackTask,
    Participant,
    QuizAttempt,
    Researcher,
    utc_now,
)
from .participant import _create_choice_schedule, _select_implemented_outcome

admin_bp = Blueprint("admin", __name__, url_prefix="/researcher")
DEBUG_STAGES = (
    ("consent", "Consent"),
    ("instructions", "Instructions"),
    ("quiz", "Comprehension check"),
    ("practice", "Practice task"),
    ("practice_rating", "Practice rating"),
    ("choices", "Reward choices"),
    ("outcome", "Selected outcome"),
    ("outcome_task", "Outcome task"),
    ("final_rating", "Final rating"),
    ("debrief", "Debrief"),
    ("complete", "Complete"),
)


def _real_participant_ids():
    return db.session.query(Participant.participant_id).filter(Participant.is_test.is_(False))


def _csv_download(filename, headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


def _iso(value):
    return value.isoformat() if value else ""


def _prepare_debug_stage(participant, stage):
    participant.is_test = True
    participant.age_eligible = True
    participant.consented = stage != "consent"
    participant.status = "in_progress"
    participant.completed_at = None
    participant.exclusion_reason = None
    participant.practice_order = json.dumps(list(design.ENABLED_N_BACK_LEVELS))

    if stage == "practice":
        for task in NBackTask.query.filter_by(participant_id=participant.participant_id, run_type="practice").all():
            db.session.delete(task)
        EffortRating.query.filter_by(participant_id=participant.participant_id, rating_type="practice").delete(synchronize_session=False)
    elif stage == "practice_rating":
        EffortRating.query.filter_by(
            participant_id=participant.participant_id,
            n_back_level=design.ENABLED_N_BACK_LEVELS[0],
            rating_type="practice",
        ).delete(synchronize_session=False)
        task = NBackTask.query.filter_by(
            participant_id=participant.participant_id,
            task_level=design.ENABLED_N_BACK_LEVELS[0],
            run_type="practice",
        ).first()
        if not task:
            now = utc_now()
            task = NBackTask(
                participant_id=participant.participant_id,
                task_level=design.ENABLED_N_BACK_LEVELS[0],
                run_type="practice",
                started_at=now,
                completed_at=now,
                actual_duration_seconds=design.PRACTICE_DURATION_SECONDS,
                sequence_json="[]",
            )
            db.session.add(task)
        else:
            task.completed_at = task.completed_at or utc_now()
            task.actual_duration_seconds = task.actual_duration_seconds or design.PRACTICE_DURATION_SECONDS

    if stage == "choices" and not Choice.query.filter_by(participant_id=participant.participant_id).first():
        _create_choice_schedule(participant)

    if stage in {"outcome", "outcome_task", "final_rating", "debrief", "complete"}:
        if not Choice.query.filter_by(participant_id=participant.participant_id).first():
            _create_choice_schedule(participant)
        choices = Choice.query.filter_by(participant_id=participant.participant_id).all()
        for choice in choices:
            choice.chosen_option = "task"
            choice.task_selected = True
            choice.timestamp = utc_now()
            choice.response_time_ms = 900
        db.session.commit()
        _select_implemented_outcome(participant)
        outcome = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
        if outcome:
            outcome.chosen_option = "task"
            outcome.task_selected = True
            outcome.credits_awarded = 0
            outcome.choice.chosen_option = "task"
            outcome.choice.task_selected = True
            if stage == "outcome_task":
                outcome.task_completed = False
                outcome.task_completed_at = None
                for task in NBackTask.query.filter_by(participant_id=participant.participant_id, run_type="outcome").all():
                    db.session.delete(task)
            if stage == "final_rating":
                EffortRating.query.filter_by(
                    participant_id=participant.participant_id,
                    n_back_level=outcome.choice.n_back_level,
                    rating_type="post_task",
                ).delete(synchronize_session=False)
            if stage == "debrief":
                DebriefResponse.query.filter_by(participant_id=participant.participant_id).delete(synchronize_session=False)

    participant.current_stage = stage
    if stage == "complete":
        participant.status = "completed"
        participant.completed_at = utc_now()
    db.session.commit()


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        destination = "admin.dashboard" if current_user.account_type == "researcher" else "participant.welcome"
        return redirect(url_for(destination))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        researcher = Researcher.query.filter_by(username=username).first()
        if researcher and researcher.check_password(password):
            login_user(researcher)
            return redirect(request.args.get("next") if request.args.get("next", "").startswith("/researcher") else url_for("admin.dashboard"))
        flash("The username or password was not recognised.", "error")
    return render_template("admin/login.html", progress_stage="welcome")


@admin_bp.post("/logout")
@researcher_required
def logout():
    logout_user()
    session.pop("researcher_debug", None)
    session.pop("participant_id", None)
    return redirect(url_for("admin.login"))


@admin_bp.get("")
@admin_bp.get("/")
@researcher_required
def dashboard():
    participants = Participant.query.order_by(Participant.created_at.desc()).all()
    real = [participant for participant in participants if not participant.is_test]
    stats = {
        "total": len(real),
        "completed": sum(participant.status == "completed" for participant in real),
        "incomplete": sum(participant.status == "in_progress" for participant in real),
        "excluded": sum(participant.status == "excluded" for participant in real),
        "withdrawn": sum(participant.status == "withdrawn" for participant in real),
        "synthetic": sum(participant.is_test for participant in participants),
    }
    return render_template("admin/dashboard.html", participants=participants, stats=stats, progress_stage="welcome")


@admin_bp.get("/participants/<participant_id>")
@researcher_required
def participant_detail(participant_id):
    participant = db.session.get(Participant, participant_id)
    if not participant:
        abort(404)
    choices = Choice.query.filter_by(participant_id=participant_id).order_by(Choice.trial_number).all()
    tasks = NBackTask.query.filter_by(participant_id=participant_id).order_by(NBackTask.started_at).all()
    ratings = EffortRating.query.filter_by(participant_id=participant_id).order_by(EffortRating.timestamp).all()
    outcome = ImplementedOutcome.query.filter_by(participant_id=participant_id).first()
    responses = NBackResponse.query.filter_by(participant_id=participant_id).order_by(
        NBackResponse.task_run_id, NBackResponse.trial_index
    ).all()
    quiz_attempts = QuizAttempt.query.filter_by(participant_id=participant_id).order_by(QuizAttempt.attempt_number).all()
    events = BrowserEvent.query.filter_by(participant_id=participant_id).order_by(BrowserEvent.timestamp).all()
    debrief = db.session.get(DebriefResponse, participant_id)
    return render_template(
        "admin/participant.html",
        participant=participant,
        choices=choices,
        tasks=tasks,
        ratings=ratings,
        outcome=outcome,
        responses=responses,
        quiz_attempts=quiz_attempts,
        events=events,
        debrief=debrief,
        progress_stage="welcome",
    )


@admin_bp.get("/debug")
@researcher_required
def debug_workspace():
    return render_template("admin/debug.html", stages=DEBUG_STAGES, progress_stage="welcome")


@admin_bp.post("/debug/start")
@researcher_required
def debug_start():
    stage = request.form.get("stage", "consent")
    if stage not in dict(DEBUG_STAGES):
        abort(400)
    participant = Participant(
        participant_id=f"TEST-{secrets.token_hex(8).upper()}",
        random_seed=secrets.randbits(32),
        is_test=True,
        current_stage="consent",
        quality_flags='["researcher_debug"]',
    )
    db.session.add(participant)
    db.session.commit()
    _prepare_debug_stage(participant, stage)
    session["participant_id"] = participant.participant_id
    session["researcher_debug"] = True
    session.permanent = True
    return redirect(url_for("participant.resume"))


@admin_bp.post("/debug/jump")
@researcher_required
def debug_jump():
    stage = request.form.get("stage", "")
    participant = db.session.get(Participant, session.get("participant_id"))
    if stage not in dict(DEBUG_STAGES):
        abort(400)
    if not session.get("researcher_debug") or not participant or not participant.is_test:
        abort(403)
    _prepare_debug_stage(participant, stage)
    return redirect(url_for("participant.resume"))


@admin_bp.post("/participants/<participant_id>/exclude")
@researcher_required
def exclude_participant(participant_id):
    participant = db.session.get(Participant, participant_id)
    if not participant:
        abort(404)
    reason = request.form.get("reason", "").strip()[:100]
    if not reason:
        flash("Enter a reason to mark the session excluded.", "error")
    elif participant.status == "withdrawn":
        flash("Withdrawn sessions cannot be reclassified here.", "error")
    else:
        participant.status = "excluded"
        participant.exclusion_reason = f"researcher: {reason}"
        db.session.commit()
        flash("Session marked excluded. Its records remain available for inspection and export.", "warning")
    return redirect(url_for("admin.participant_detail", participant_id=participant_id))


@admin_bp.get("/export/participants.csv")
@researcher_required
def export_participants():
    rows = Participant.query.filter(Participant.is_test.is_(False)).order_by(Participant.created_at).all()
    headers = ["participant_id", "session_creation_time", "completion_time", "consent_status", "eligibility_status", "experiment_status", "current_stage", "exclusion_reason", "randomisation_seed", "quality_flags", "is_test"]
    values = ([row.participant_id, _iso(row.created_at), _iso(row.completed_at), row.consented, row.age_eligible, row.status, row.current_stage, row.exclusion_reason or "", row.random_seed, row.quality_flags, False] for row in rows)
    return _csv_download("participants.csv", headers, values)


@admin_bp.get("/export/choices.csv")
@researcher_required
def export_choices():
    rows = Choice.query.filter(Choice.participant_id.in_(_real_participant_ids())).order_by(Choice.participant_id, Choice.trial_number).all()
    headers = ["participant_id", "trial_number", "condition_id", "n_back_level", "duration_minutes", "alternative_reward_x", "task_reward_r", "chosen_option", "task_selected", "no_task_position", "task_position", "is_catch_trial", "catch_type", "trial_timestamp", "response_time_ms"]
    values = ([row.participant_id, row.trial_number, row.condition_id, row.n_back_level, row.duration_minutes, row.alternative_reward, row.task_reward, row.chosen_option, row.task_selected, row.no_task_position, row.task_position, row.is_catch, row.catch_type or "", _iso(row.timestamp), row.response_time_ms] for row in rows)
    return _csv_download("choices.csv", headers, values)


@admin_bp.get("/export/nback-responses.csv")
@researcher_required
def export_nback_responses():
    rows = NBackResponse.query.join(Participant).join(NBackTask).filter(
        Participant.is_test.is_(False)
    ).order_by(NBackResponse.participant_id, NBackResponse.task_run_id, NBackResponse.trial_index).all()
    headers = ["participant_id", "run_type", "task_level", "trial_index", "stimulus", "correct_response_target", "actual_response", "accuracy", "reaction_time_ms", "response_timestamp", "actual_task_duration_seconds"]
    values = ([row.participant_id, row.task_run.run_type, row.task_level, row.trial_index, row.stimulus, row.correct_response, row.actual_response or "", row.accuracy, row.reaction_time_ms, _iso(row.timestamp), row.task_run.actual_duration_seconds] for row in rows)
    return _csv_download("nback_responses.csv", headers, values)


@admin_bp.get("/export/effort-ratings.csv")
@researcher_required
def export_effort_ratings():
    rows = EffortRating.query.join(Participant).filter(Participant.is_test.is_(False)).order_by(
        EffortRating.participant_id, EffortRating.timestamp
    ).all()
    headers = ["participant_id", "n_back_level", "rating_type", "rating_0_100", "task_run_type", "timestamp"]
    values = ([row.participant_id, row.n_back_level, row.rating_type, row.rating, row.task_run.run_type if row.task_run else "", _iso(row.timestamp)] for row in rows)
    return _csv_download("effort_ratings.csv", headers, values)


@admin_bp.get("/export/implemented-outcomes.csv")
@researcher_required
def export_implemented_outcomes():
    rows = ImplementedOutcome.query.join(Participant).filter(Participant.is_test.is_(False)).order_by(
        ImplementedOutcome.participant_id
    ).all()
    headers = ["participant_id", "selected_trial_number", "condition_id", "n_back_level", "duration_minutes", "chosen_option", "task_selected", "credits_awarded", "task_completed", "selected_at", "task_completed_at"]
    values = ([row.participant_id, row.choice.trial_number, row.choice.condition_id, row.choice.n_back_level, row.choice.duration_minutes, row.chosen_option, row.task_selected, row.credits_awarded, row.task_completed, _iso(row.selected_at), _iso(row.task_completed_at)] for row in rows)
    return _csv_download("implemented_outcomes.csv", headers, values)