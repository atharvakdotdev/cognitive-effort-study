"""Create labelled synthetic records for researcher interface checks only."""

import json
import random
from datetime import timedelta

from . import db, experiment_design as design
from .experiment import adjust_offer, build_trial_schedule, catch_trial_rewards, initial_offer
from .models import Choice, EffortRating, Participant, utc_now


def seed_demo_data() -> int:
    if Participant.query.filter_by(is_test=True).count():
        return 0
    now = utc_now()
    created = 0
    for index, status in enumerate(("completed", "completed", "excluded"), start=1):
        seed = 1200 + index
        participant = Participant(
            participant_id=f"SYNTHETIC-{index:03d}",
            created_at=now - timedelta(days=index),
            completed_at=now - timedelta(hours=index) if status == "completed" else None,
            consented=True,
            age_eligible=True,
            status=status,
            current_stage="complete" if status == "completed" else "excluded",
            exclusion_reason="synthetic demonstration record" if status == "excluded" else None,
            random_seed=seed,
            is_test=True,
            practice_order=json.dumps(list(design.N_BACK_LEVELS)),
        )
        db.session.add(participant)
        db.session.flush()
        rng = random.Random(seed)
        schedule = build_trial_schedule(rng)
        staircases = {}
        for trial in schedule:
            if trial["is_catch"]:
                alternative, reward, level, duration = catch_trial_rewards(trial["catch_type"])
                chosen = "no_task" if trial["catch_type"] == "dominated" else "task"
            else:
                key = trial["condition_id"]
                reward = trial["reward"]
                if key not in staircases:
                    staircases[key] = {
                        "current_x": initial_offer(rng, reward),
                        "step_index": 0,
                    }
                alternative = staircases[key]["current_x"]
                level, duration = trial["level"], trial["duration"]
                chosen = "task" if rng.random() < max(0.15, 0.78 - level * 0.12 - duration * 0.035) else "no_task"
                staircases[key]["current_x"] = adjust_offer(
                    alternative,
                    chose_task=chosen == "task",
                    step_index=staircases[key]["step_index"],
                    task_reward=reward,
                )
                staircases[key]["step_index"] = min(
                    staircases[key]["step_index"] + 1,
                    len(design.ADAPTIVE_STEPS) - 1,
                )
            db.session.add(
                Choice(
                    participant_id=participant.participant_id,
                    trial_number=trial["trial_number"],
                    condition_id=trial["condition_id"],
                    n_back_level=level,
                    duration_minutes=duration,
                    alternative_reward=alternative,
                    task_reward=reward,
                    chosen_option=chosen,
                    task_selected=chosen == "task",
                    no_task_position=trial["no_task_position"],
                    task_position=trial["task_position"],
                    is_catch=trial["is_catch"],
                    catch_type=trial["catch_type"],
                    timestamp=now - timedelta(hours=index, minutes=len(schedule) - trial["trial_number"]),
                    response_time_ms=rng.uniform(700, 8000),
                )
            )
        for level in design.N_BACK_LEVELS:
            db.session.add(
                EffortRating(
                    participant_id=participant.participant_id,
                    n_back_level=level,
                    rating_type="practice",
                    rating=min(100, 18 + level * 20 + rng.randrange(12)),
                    timestamp=now - timedelta(days=index, minutes=level),
                )
            )
        created += 1
    db.session.commit()
    return created