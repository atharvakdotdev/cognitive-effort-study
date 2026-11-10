"""Transparent descriptive summaries; no model fitting or automatic exclusions."""

from collections import defaultdict

from flask import Blueprint, render_template

from . import experiment_design as design
from .auth import researcher_required
from .models import Choice, EffortRating, NBackResponse, Participant

analysis_bp = Blueprint("analysis", __name__, url_prefix="/researcher/analysis")


@analysis_bp.get("")
@analysis_bp.get("/")
@researcher_required
def analysis_page():
    participants = Participant.query.filter(Participant.is_test.is_(False)).all()
    participant_ids = [participant.participant_id for participant in participants]
    choices = Choice.query.filter(
        Choice.participant_id.in_(participant_ids),
        Choice.is_catch.is_(False),
        Choice.chosen_option.isnot(None),
    ).all() if participant_ids else []

    condition_counts = defaultdict(lambda: [0, 0])
    duration_counts = defaultdict(lambda: [0, 0])
    level_counts = defaultdict(lambda: [0, 0])
    for choice in choices:
        for key, counts in (
            ((choice.n_back_level, choice.duration_minutes), condition_counts),
            (choice.duration_minutes, duration_counts),
            (choice.n_back_level, level_counts),
        ):
            counts[key][0] += int(choice.task_selected)
            counts[key][1] += 1

    def rate(pair):
        accepted, total = pair
        return round(100 * accepted / total, 1) if total else None

    condition_rows = [
        {"level": level, "duration": duration, "accepted": condition_counts[(level, duration)][0], "total": condition_counts[(level, duration)][1], "rate": rate(condition_counts[(level, duration)])}
        for level in design.ENABLED_N_BACK_LEVELS
        for duration in design.DURATIONS_MINUTES
    ]
    duration_rows = [
        {"duration": duration, "rate": rate(duration_counts[duration]), "total": duration_counts[duration][1]}
        for duration in design.DURATIONS_MINUTES
    ]
    level_rows = [
        {"level": level, "rate": rate(level_counts[level]), "total": level_counts[level][1]}
        for level in design.ENABLED_N_BACK_LEVELS
    ]

    ratings = EffortRating.query.filter(
        EffortRating.participant_id.in_(participant_ids), EffortRating.rating_type == "practice"
    ).all() if participant_ids else []
    rating_values = defaultdict(list)
    for rating in ratings:
        rating_values[rating.n_back_level].append(rating.rating)
    rating_rows = [
        {"level": level, "average": round(sum(rating_values[level]) / len(rating_values[level]), 1) if rating_values[level] else None, "total": len(rating_values[level])}
        for level in design.ENABLED_N_BACK_LEVELS
    ]

    responses = NBackResponse.query.filter(NBackResponse.participant_id.in_(participant_ids)).all() if participant_ids else []
    performance = defaultdict(lambda: {"correct": 0, "count": 0, "reaction_times": []})
    for response in responses:
        summary = performance[response.task_level]
        summary["correct"] += int(response.accuracy)
        summary["count"] += 1
        if response.reaction_time_ms is not None:
            summary["reaction_times"].append(response.reaction_time_ms)
    performance_rows = []
    for level in design.ENABLED_N_BACK_LEVELS:
        summary = performance[level]
        performance_rows.append({
            "level": level,
            "accuracy": round(100 * summary["correct"] / summary["count"], 1) if summary["count"] else None,
            "reaction_time": round(sum(summary["reaction_times"]) / len(summary["reaction_times"]), 1) if summary["reaction_times"] else None,
            "total": summary["count"],
        })

    completed = sum(participant.status == "completed" for participant in participants)
    excluded = sum(participant.status == "excluded" for participant in participants)
    return render_template(
        "admin/analysis.html",
        participants=len(participants),
        completed=completed,
        excluded=excluded,
        completion_rate=round(100 * completed / len(participants), 1) if participants else None,
        condition_rows=condition_rows,
        duration_rows=duration_rows,
        level_rows=level_rows,
        rating_rows=rating_rows,
        performance_rows=performance_rows,
        progress_stage="welcome",
    )