"""Visible, non-destructive participant quality checks."""

import json

from . import experiment_design as design
from . import db
from .models import BrowserEvent, Choice, EffortRating, NBackResponse, NBackTask


def get_flags(participant) -> list[str]:
    try:
        return json.loads(participant.quality_flags or "[]")
    except (TypeError, json.JSONDecodeError):
        return []


def add_flag(participant, flag: str) -> None:
    flags = get_flags(participant)
    if flag not in flags:
        flags.append(flag)
    participant.quality_flags = json.dumps(flags)


def evaluate_quality(participant) -> list[str]:
    flags = get_flags(participant)
    if participant.status == "completed":
        flags = [flag for flag in flags if flag != "incomplete_experiment"]
    elif participant.status == "in_progress" and "incomplete_experiment" not in flags:
        flags.append("incomplete_experiment")
    practice = NBackTask.query.filter_by(
        participant_id=participant.participant_id, task_level=0, run_type="practice"
    ).first()
    if practice and practice.responses:
        accuracy = sum(response.accuracy for response in practice.responses) / len(practice.responses)
        if accuracy < design.QUALITY_THRESHOLDS["zero_back_min_accuracy"]:
            flags.append("zero_back_practice_below_threshold")

    choices = Choice.query.filter_by(participant_id=participant.participant_id, is_catch=True).all()
    catch_failures = sum(
        choice.chosen_option != ("no_task" if choice.catch_type == "dominated" else "task")
        for choice in choices
        if choice.chosen_option is not None
    )
    if len(choices) == sum(design.CATCH_TRIALS.values()) and catch_failures > design.QUALITY_THRESHOLDS["catch_failures_max"]:
        flags.append("more_than_one_catch_failure")

    responses = NBackResponse.query.filter_by(participant_id=participant.participant_id).order_by(
        NBackResponse.task_run_id, NBackResponse.trial_index
    ).all()
    if responses:
        missing_fraction = sum(response.actual_response is None for response in responses) / len(responses)
        if missing_fraction > design.QUALITY_THRESHOLDS["missing_response_fraction_max"]:
            flags.append("excessive_missing_responses")

        longest_streak = 0
        current_streak = 0
        previous_response = object()
        for response in responses:
            if response.actual_response is not None and response.actual_response == previous_response:
                current_streak += 1
            else:
                current_streak = 1 if response.actual_response is not None else 0
            previous_response = response.actual_response
            longest_streak = max(longest_streak, current_streak)
        if longest_streak >= design.QUALITY_THRESHOLDS["repetitive_response_streak"]:
            flags.append("suspiciously_repetitive_responses")

    focus_losses = BrowserEvent.query.filter(
        BrowserEvent.participant_id == participant.participant_id,
        BrowserEvent.event_type.in_(("visibility_hidden", "window_blur")),
    ).count()
    if focus_losses >= design.QUALITY_THRESHOLDS["focus_loss_count"]:
        flags.append("repeated_focus_loss")

    participant.quality_flags = json.dumps(sorted(set(flags)))
    db.session.commit()
    return get_flags(participant)