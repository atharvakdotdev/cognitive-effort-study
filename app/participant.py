"""Consent-led participant workflow and server-validated experiment endpoints."""

import json
import random
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from . import db
from . import experiment_design as design
from .data_quality import add_flag, evaluate_quality
from .experiment import (
    adjust_offer,
    build_trial_schedule,
    catch_trial_rewards,
    implemented_outcome,
    initial_offer,
)
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
    UserAccount,
    utc_now,
)
from .nback import generate_sequence, validate_responses

participant_bp = Blueprint("participant", __name__)

QUIZ_ANSWERS = {"q1": "one", "q2": "both", "q3": "withdraw", "q4": "adult"}
STAGE_ENDPOINTS = {
    "consent": "consent",
    "instructions": "instructions",
    "quiz": "quiz",
    "practice": "resume",
    "practice_rating": "resume",
    "choices": "choice",
    "outcome": "outcome",
    "outcome_task": "outcome_task",
    "final_rating": "final_rating",
    "debrief": "debrief",
    "complete": "debrief",
}


def current_participant():
    participant_id = session.get("participant_id")
    return db.session.get(Participant, participant_id) if participant_id else None


def require_participant():
    participant = current_participant()
    if participant is None:
        return None
    return participant


def _is_researcher_debug(participant):
    return bool(
        participant
        and participant.is_test
        and session.get("researcher_debug")
        and current_user.is_authenticated
        and getattr(current_user, "account_type", None) == "researcher"
    )


def _json_value(value, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback


def _redirect_for_stage(participant):
    stage = participant.current_stage
    if stage == "practice":
        order = _json_value(participant.practice_order, list(design.ENABLED_N_BACK_LEVELS))
        completed = {
            task.task_level
            for task in NBackTask.query.filter_by(
                participant_id=participant.participant_id, run_type="practice"
            ).all()
            if task.completed_at
        }
        next_level = next((level for level in order if level not in completed), order[-1])
        return redirect(url_for("participant.practice", level=next_level))
    if stage == "practice_rating":
        task = NBackTask.query.filter_by(
            participant_id=participant.participant_id, run_type="practice"
        ).filter(NBackTask.completed_at.isnot(None)).order_by(NBackTask.completed_at.desc()).first()
        if task:
            return redirect(url_for("participant.practice_rating", level=task.task_level))
    endpoint = STAGE_ENDPOINTS.get(stage)
    if endpoint == "choice":
        return redirect(url_for("participant.choice"))
    if endpoint:
        return redirect(url_for(f"participant.{endpoint}"))
    if stage == "withdrawn":
        return render_template("withdrawn.html")
    if stage == "excluded":
        return render_template("terminated.html", reason=participant.exclusion_reason)
    return render_template("debrief.html", complete=True)


def _progress_stage():
    participant = current_participant()
    return participant.current_stage if participant else "welcome"


def _create_choice_schedule(participant):
    rng = random.Random(participant.random_seed)
    schedule = build_trial_schedule(rng)
    offer_rng = random.Random(participant.random_seed ^ 0xA55A)
    condition_offers = {}
    for row in schedule:
        if row["is_catch"]:
            alternative, reward, level, duration = catch_trial_rewards(row["catch_type"])
        else:
            key = row["condition_id"]
            if key not in condition_offers:
                condition_offers[key] = initial_offer(offer_rng)
            alternative = condition_offers[key] if row["within_condition_trial"] == 1 else None
            reward, level, duration = design.TASK_REWARD, row["level"], row["duration"]
        db.session.add(
            Choice(
                participant_id=participant.participant_id,
                trial_number=row["trial_number"],
                condition_id=row["condition_id"],
                n_back_level=level,
                duration_minutes=duration,
                alternative_reward=alternative,
                task_reward=reward,
                no_task_position=row["no_task_position"],
                task_position=row["task_position"],
                is_catch=row["is_catch"],
                catch_type=row["catch_type"],
            )
        )
    participant.current_stage = "choices"
    db.session.commit()


def _task_run(participant, level, run_type, duration_seconds):
    task = NBackTask.query.filter_by(
        participant_id=participant.participant_id, task_level=level, run_type=run_type
    ).first()
    if task is None:
        task = NBackTask(
            participant_id=participant.participant_id,
            task_level=level,
            run_type=run_type,
            started_at=utc_now(),
        )
        db.session.add(task)
        db.session.flush()
        count = max(1, round(duration_seconds * 1000 / (design.STIMULUS_DURATION_MS + design.INTER_STIMULUS_INTERVAL_MS)))
        task.sequence_json = json.dumps(
            generate_sequence(level, count, random.Random(participant.random_seed + task.id * 37 + level))
        )
        db.session.commit()
    return task


def _started_seconds_ago(task):
    started_at = task.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return max(0.0, (utc_now() - started_at).total_seconds())


def _record_task_responses(participant, task, payload, expected_duration):
    if task.completed_at:
        return False, "This task has already been recorded."
    elapsed = _started_seconds_ago(task)
    if elapsed < expected_duration - 1.5:
        return False, "The task timer has not finished yet."
    sequence = _json_value(task.sequence_json, [])
    try:
        validated = validate_responses(task.task_level, sequence, payload.get("responses"))
    except (ValueError, AttributeError) as error:
        return False, str(error)

    completed_at = utc_now()
    for response in validated:
        stimulus_time = task.started_at
        if stimulus_time.tzinfo is None:
            stimulus_time = stimulus_time.replace(tzinfo=timezone.utc)
        stimulus_time += timedelta(
            milliseconds=(response["trial_index"] - 1)
            * (design.STIMULUS_DURATION_MS + design.INTER_STIMULUS_INTERVAL_MS)
        )
        db.session.add(
            NBackResponse(
                task_run_id=task.id,
                participant_id=participant.participant_id,
                task_level=task.task_level,
                trial_index=response["trial_index"],
                stimulus=response["stimulus"],
                correct_response=response["correct_response"],
                actual_response=response["actual_response"],
                accuracy=response["accuracy"],
                reaction_time_ms=response["reaction_time_ms"],
                timestamp=stimulus_time,
            )
        )
    task.completed_at = completed_at
    task.actual_duration_seconds = (completed_at - task.started_at.replace(tzinfo=timezone.utc)).total_seconds() if task.started_at.tzinfo is None else (completed_at - task.started_at).total_seconds()
    db.session.commit()
    return True, None


def _task_context(task, duration_seconds, next_url):
    return {
        "task_level": task.task_level,
        "run_type": task.run_type,
        "sequence": _json_value(task.sequence_json, []),
        "duration_seconds": duration_seconds,
        "stimulus_duration_ms": design.STIMULUS_DURATION_MS,
        "isi_ms": design.INTER_STIMULUS_INTERVAL_MS,
        "next_url": next_url,
        "csrf_token": __import__("flask_wtf.csrf", fromlist=["generate_csrf"]).generate_csrf(),
    }


@participant_bp.get("/")
def welcome():
    participant = current_participant()
    if participant:
        return _redirect_for_stage(participant)
    return render_template("welcome.html", progress_stage="welcome")


@participant_bp.route("/consent", methods=["GET", "POST"])
def consent():
    participant = current_participant()
    researcher_debug = _is_researcher_debug(participant)
    if participant and not researcher_debug:
        return _redirect_for_stage(participant)
    if request.method == "POST":
        age_eligible = request.form.get("age_confirmed") == "yes"
        has_read = request.form.get("read_confirmed") == "yes"
        voluntary = request.form.get("agree_confirmed") == "yes"
        seed = secrets.randbits(32)
        is_test = current_app.config.get("EXPERIMENT_TEST_MODE", False)
        if researcher_debug:
            participant.random_seed = seed
            participant.age_eligible = age_eligible
            participant.consented = age_eligible and has_read and voluntary
        else:
            participant = Participant(
                participant_id=f"{'TEST' if is_test else 'P'}-{secrets.token_hex(8).upper()}",
                random_seed=seed,
                age_eligible=age_eligible,
                consented=age_eligible and has_read and voluntary,
                is_test=is_test,
            )
        if not age_eligible:
            participant.status = "excluded"
            participant.current_stage = "excluded"
            participant.exclusion_reason = "under_18_or_age_not_confirmed"
        elif not (has_read and voluntary):
            participant.status = "withdrawn"
            participant.current_stage = "withdrawn"
        else:
            participant.current_stage = "instructions"
            participant.quality_flags = json.dumps(["incomplete_experiment"])
            participant.practice_order = json.dumps(
                random.Random(seed).sample(list(design.ENABLED_N_BACK_LEVELS), len(design.ENABLED_N_BACK_LEVELS))
            )
        db.session.add(participant)
        db.session.commit()
        if participant.status == "in_progress" or researcher_debug:
            session["participant_id"] = participant.participant_id
            session.permanent = True
            if current_user.is_authenticated and getattr(current_user, "account_type", None) == "user" and participant.status == "in_progress":
                account = db.session.get(UserAccount, current_user.id)
                account.participant_id = participant.participant_id
                db.session.commit()
        else:
            session.pop("participant_id", None)
        return _redirect_for_stage(participant)
    return render_template("consent.html", progress_stage="consent")


@participant_bp.get("/resume")
def resume():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    return _redirect_for_stage(participant)


@participant_bp.route("/instructions", methods=["GET", "POST"])
def instructions():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    if participant.current_stage != "instructions":
        return _redirect_for_stage(participant)
    if request.method == "POST":
        participant.current_stage = "quiz"
        db.session.commit()
        return redirect(url_for("participant.quiz"))
    return render_template("instructions.html", progress_stage=participant.current_stage)


@participant_bp.route("/quiz", methods=["GET", "POST"])
def quiz():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    if participant.current_stage != "quiz":
        return _redirect_for_stage(participant)
    if request.method == "POST":
        answers = {key: request.form.get(key, "") for key in QUIZ_ANSWERS}
        attempt_number = participant.quiz_attempts + 1
        passed = answers == QUIZ_ANSWERS
        participant.quiz_attempts = attempt_number
        db.session.add(
            QuizAttempt(
                participant_id=participant.participant_id,
                attempt_number=attempt_number,
                answers_json=json.dumps(answers),
                passed=passed,
            )
        )
        if passed:
            participant.current_stage = "practice"
        elif attempt_number >= 2:
            participant.status = "excluded"
            participant.current_stage = "excluded"
            participant.exclusion_reason = "failed_comprehension_quiz"
            add_flag(participant, "failed_comprehension_quiz")
        db.session.commit()
        if passed:
            return redirect(url_for("participant.resume"))
        if attempt_number >= 2:
            return render_template("terminated.html", reason=participant.exclusion_reason)
        flash("One or more answers were incorrect. Review the explanation and try once more.", "warning")
    return render_template("quiz.html", progress_stage=participant.current_stage, retry=participant.quiz_attempts > 0)


@participant_bp.get("/practice/<int:level>")
def practice(level):
    participant = require_participant()
    if not participant or participant.current_stage != "practice":
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    order = _json_value(participant.practice_order, list(design.ENABLED_N_BACK_LEVELS))
    completed_levels = {
        task.task_level
        for task in NBackTask.query.filter_by(participant_id=participant.participant_id, run_type="practice").all()
        if task.completed_at
    }
    expected_level = next((item for item in order if item not in completed_levels), None)
    if level != expected_level:
        return redirect(url_for("participant.resume"))
    duration = design.PRACTICE_DURATION_SECONDS
    task = _task_run(participant, level, "practice", duration)
    if task.completed_at:
        return redirect(url_for("participant.practice_rating", level=level))
    return render_template(
        "task.html",
        progress_stage=participant.current_stage,
        task=_task_context(task, duration, url_for("participant.practice_complete", level=level)),
        heading=f"{level}-back practice",
        task_description="Respond to each letter using the two keys shown below.",
    )


@participant_bp.post("/practice/<int:level>/complete")
def practice_complete(level):
    participant = require_participant()
    if not participant or participant.current_stage != "practice":
        return jsonify(error="This practice block is not current."), 409
    task = NBackTask.query.filter_by(
        participant_id=participant.participant_id, task_level=level, run_type="practice"
    ).first()
    if not task:
        return jsonify(error="Practice task not found."), 404
    success, error = _record_task_responses(participant, task, request.get_json(silent=True) or {}, design.PRACTICE_DURATION_SECONDS)
    if not success:
        return jsonify(error=error), 409
    participant.current_stage = "practice_rating"
    db.session.commit()
    return jsonify(next_url=url_for("participant.practice_rating", level=level))


@participant_bp.route("/practice-rating/<int:level>", methods=["GET", "POST"])
def practice_rating(level):
    participant = require_participant()
    if not participant or participant.current_stage != "practice_rating":
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    task = NBackTask.query.filter_by(
        participant_id=participant.participant_id, task_level=level, run_type="practice"
    ).first()
    if not task or not task.completed_at or EffortRating.query.filter_by(
        participant_id=participant.participant_id, n_back_level=level, rating_type="practice"
    ).first():
        return redirect(url_for("participant.resume"))
    if request.method == "POST":
        try:
            rating = int(request.form.get("rating", ""))
        except ValueError:
            rating = -1
        if not 0 <= rating <= 100:
            flash("Choose a rating from 0 to 100.", "error")
        else:
            db.session.add(
                EffortRating(
                    participant_id=participant.participant_id,
                    n_back_level=level,
                    rating_type="practice",
                    rating=rating,
                    task_run_id=task.id,
                )
            )
            order = _json_value(participant.practice_order, list(design.ENABLED_N_BACK_LEVELS))
            next_level = next((item for item in order if not EffortRating.query.filter_by(
                participant_id=participant.participant_id, n_back_level=item, rating_type="practice"
            ).first() and item != level), None)
            if next_level is None:
                _create_choice_schedule(participant)
            else:
                participant.current_stage = "practice"
                db.session.commit()
            return redirect(url_for("participant.resume"))
    accuracy = sum(response.accuracy for response in task.responses) / max(1, len(task.responses)) * 100
    return render_template(
        "effort_rating.html",
        progress_stage=participant.current_stage,
        level=level,
        rating_type="practice",
        accuracy=round(accuracy),
    )


@participant_bp.get("/choice")
def choice():
    participant = require_participant()
    if not participant or participant.current_stage != "choices":
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    current = Choice.query.filter_by(
        participant_id=participant.participant_id, chosen_option=None
    ).order_by(Choice.trial_number).first()
    if current is None:
        _select_implemented_outcome(participant)
        return redirect(url_for("participant.outcome"))
    return render_template(
        "choice.html",
        progress_stage=participant.current_stage,
        choice=current,
        total=design.CHOICES_PER_CONDITION * len(design.ENABLED_N_BACK_LEVELS) * len(design.DURATIONS_MINUTES) + sum(design.CATCH_TRIALS.values()),
        csrf_value=__import__("flask_wtf.csrf", fromlist=["generate_csrf"]).generate_csrf(),
    )


@participant_bp.post("/choice/<int:trial_number>")
def submit_choice(trial_number):
    participant = require_participant()
    if not participant or participant.current_stage != "choices":
        return jsonify(error="Choices are not currently open."), 409
    current = Choice.query.filter_by(
        participant_id=participant.participant_id, chosen_option=None
    ).order_by(Choice.trial_number).first()
    if current is None or current.trial_number != trial_number:
        return jsonify(error="This trial is not the next available choice."), 409
    chosen_option = request.form.get("chosen_option")
    if chosen_option not in ("task", "no_task"):
        abort(400)
    try:
        response_time = float(request.form.get("response_time_ms", ""))
    except ValueError:
        abort(400)
    if not 0 <= response_time <= 300000:
        abort(400)
    current.chosen_option = chosen_option
    current.task_selected = chosen_option == "task"
    current.timestamp = utc_now()
    current.response_time_ms = response_time

    if not current.is_catch:
        next_condition_choice = Choice.query.filter(
            Choice.participant_id == participant.participant_id,
            Choice.condition_id == current.condition_id,
            Choice.is_catch.is_(False),
            Choice.chosen_option.is_(None),
            Choice.trial_number > current.trial_number,
        ).order_by(Choice.trial_number).first()
        if next_condition_choice:
            next_condition_choice.alternative_reward = adjust_offer(
                current.alternative_reward,
                chose_task=current.task_selected,
                step_index=(current.trial_number and _condition_trial_index(current) - 1),
            )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="This choice has already been recorded."), 409

    if Choice.query.filter_by(participant_id=participant.participant_id, chosen_option=None).count() == 0:
        _select_implemented_outcome(participant)
        return redirect(url_for("participant.outcome"))
    return redirect(url_for("participant.choice"))


def _condition_trial_index(choice):
    return Choice.query.filter_by(
        participant_id=choice.participant_id,
        condition_id=choice.condition_id,
        is_catch=False,
    ).filter(Choice.trial_number <= choice.trial_number).count()


def _select_implemented_outcome(participant):
    if ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first():
        return
    completed_main = Choice.query.filter_by(
        participant_id=participant.participant_id, is_catch=False
    ).filter(Choice.chosen_option.isnot(None)).order_by(Choice.trial_number).all()
    if len(completed_main) != len(design.ENABLED_N_BACK_LEVELS) * len(design.DURATIONS_MINUTES) * design.CHOICES_PER_CONDITION:
        return
    selected = random.Random(participant.random_seed ^ 0xC0FFEE).choice(completed_main)
    outcome_data = implemented_outcome(
        {
            "trial_number": selected.trial_number,
            "condition_id": selected.condition_id,
            "chosen_option": selected.chosen_option,
            "task_reward": selected.task_reward,
            "alternative_reward": selected.alternative_reward,
            "level": selected.n_back_level,
            "duration": selected.duration_minutes,
            "is_catch": selected.is_catch,
        }
    )
    db.session.add(
        ImplementedOutcome(
            participant_id=participant.participant_id,
            choice_id=selected.id,
            chosen_option=selected.chosen_option,
            task_selected=outcome_data["task_selected"],
            credits_awarded=0.0 if outcome_data["task_selected"] else outcome_data["credits_awarded"],
        )
    )
    participant.current_stage = "outcome"
    db.session.commit()


@participant_bp.get("/outcome")
def outcome():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    if participant.current_stage == "final_rating":
        return redirect(url_for("participant.final_rating"))
    if participant.current_stage != "outcome":
        return _redirect_for_stage(participant)
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
    if not outcome_record:
        return redirect(url_for("participant.resume"))
    return render_template("outcome.html", progress_stage=participant.current_stage, outcome=outcome_record)


@participant_bp.post("/outcome/continue")
def outcome_continue():
    participant = require_participant()
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first() if participant else None
    if not participant or participant.current_stage != "outcome" or not outcome_record:
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    participant.current_stage = "outcome_task" if outcome_record.task_selected else "final_rating"
    db.session.commit()
    return redirect(url_for("participant.resume"))


@participant_bp.get("/outcome/task")
def outcome_task():
    participant = require_participant()
    if not participant or participant.current_stage != "outcome_task":
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
    if not outcome_record or not outcome_record.task_selected:
        return redirect(url_for("participant.resume"))
    if outcome_record.task_completed:
        participant.current_stage = "final_rating"
        db.session.commit()
        return redirect(url_for("participant.final_rating"))
    task = _task_run(
        participant,
        outcome_record.choice.n_back_level,
        "outcome",
        outcome_record.choice.duration_minutes * 60,
    )
    return render_template(
        "task.html",
        progress_stage=participant.current_stage,
        task=_task_context(task, outcome_record.choice.duration_minutes * 60, url_for("participant.outcome_task_complete")),
        heading="Your selected task",
        task_description=f"Complete the {task.task_level}-back task for {outcome_record.choice.duration_minutes} minutes to receive 10 credits.",
    )


@participant_bp.post("/outcome/task/complete")
def outcome_task_complete():
    participant = require_participant()
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first() if participant else None
    if not participant or participant.current_stage != "outcome_task" or not outcome_record:
        return jsonify(error="The selected task is not current."), 409
    task = NBackTask.query.filter_by(
        participant_id=participant.participant_id,
        task_level=outcome_record.choice.n_back_level,
        run_type="outcome",
    ).first()
    if not task:
        return jsonify(error="Task record not found."), 404
    success, error = _record_task_responses(
        participant, task, request.get_json(silent=True) or {}, outcome_record.choice.duration_minutes * 60
    )
    if not success:
        return jsonify(error=error), 409
    outcome_record.task_completed = True
    outcome_record.task_completed_at = utc_now()
    outcome_record.credits_awarded = outcome_record.choice.task_reward
    participant.current_stage = "final_rating"
    db.session.commit()
    return jsonify(next_url=url_for("participant.final_rating"))


@participant_bp.route("/final-rating", methods=["GET", "POST"])
def final_rating():
    participant = require_participant()
    if not participant or participant.current_stage != "final_rating":
        return _redirect_for_stage(participant) if participant else redirect(url_for("participant.welcome"))
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
    if not outcome_record:
        return redirect(url_for("participant.resume"))
    level = outcome_record.choice.n_back_level
    rating_type = "post_task" if outcome_record.task_selected else "expected"
    if request.method == "POST":
        try:
            rating = int(request.form.get("rating", ""))
        except ValueError:
            rating = -1
        if not 0 <= rating <= 100:
            flash("Choose a rating from 0 to 100.", "error")
        else:
            task_run = NBackTask.query.filter_by(
                participant_id=participant.participant_id, run_type="outcome"
            ).first()
            db.session.add(
                EffortRating(
                    participant_id=participant.participant_id,
                    n_back_level=level,
                    rating_type=rating_type,
                    rating=rating,
                    task_run_id=task_run.id if task_run else None,
                )
            )
            participant.current_stage = "debrief"
            db.session.commit()
            return redirect(url_for("participant.debrief"))
    return render_template(
        "effort_rating.html",
        progress_stage=participant.current_stage,
        level=level,
        rating_type=rating_type,
        accuracy=None,
    )


@participant_bp.route("/debrief", methods=["GET", "POST"])
def debrief():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    if participant.current_stage == "complete":
        outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
        return render_template("debrief.html", complete=True, outcome=outcome_record, progress_stage="complete")
    if participant.current_stage != "debrief":
        return _redirect_for_stage(participant)
    if request.method == "POST":
        feedback = request.form.get("feedback", "").strip()[:4000]
        db.session.add(DebriefResponse(participant_id=participant.participant_id, open_feedback=feedback))
        participant.completed_at = utc_now()
        participant.status = "completed"
        participant.current_stage = "complete"
        evaluate_quality(participant)
        db.session.commit()
        return redirect(url_for("participant.debrief"))
    outcome_record = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).first()
    return render_template("debrief.html", complete=False, outcome=outcome_record, progress_stage="debrief")


@participant_bp.post("/withdraw")
def withdraw():
    participant = require_participant()
    if not participant:
        return redirect(url_for("participant.welcome"))
    if participant.status != "in_progress" or participant.current_stage == "complete":
        return _redirect_for_stage(participant)
    participant_id = participant.participant_id
    NBackResponse.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    EffortRating.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    NBackTask.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    ImplementedOutcome.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    Choice.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    QuizAttempt.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    BrowserEvent.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    DebriefResponse.query.filter_by(participant_id=participant_id).delete(synchronize_session=False)
    participant.status = "withdrawn"
    participant.current_stage = "withdrawn"
    participant.quality_flags = "[]"
    db.session.commit()
    session.pop("participant_id", None)
    return render_template("withdrawn.html")


@participant_bp.post("/events/focus")
def focus_event():
    participant = require_participant()
    if not participant or participant.status != "in_progress":
        return jsonify(error="No active participant session."), 401
    event_type = (request.get_json(silent=True) or {}).get("event_type")
    if event_type not in ("visibility_hidden", "window_blur"):
        return jsonify(error="Unknown browser event."), 400
    db.session.add(
        BrowserEvent(
            participant_id=participant.participant_id,
            event_type=event_type,
            stage=participant.current_stage,
        )
    )
    db.session.commit()
    return jsonify(recorded=True)