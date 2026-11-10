"""Pure experiment-design logic, independent of Flask and persistence."""

from __future__ import annotations

import random
from typing import Any

from . import experiment_design as design


def condition_id(level: int, duration: int) -> str:
    return f"E{level}_T{duration}"


def _option_positions(rng: random.Random) -> tuple[str, str]:
    task_position = rng.choice(("left", "right"))
    no_task_position = "right" if task_position == "left" else "left"
    return no_task_position, task_position


def build_trial_schedule(rng: random.Random) -> list[dict[str, Any]]:
    """Build a dispersed schedule of main and control trials."""
    main_trials: list[dict[str, Any]] = []
    for level in design.ENABLED_N_BACK_LEVELS:
        for duration in design.DURATIONS_MINUTES:
            for within_condition_trial in range(1, design.CHOICES_PER_CONDITION + 1):
                no_task_position, task_position = _option_positions(rng)
                main_trials.append(
                    {
                        "condition_id": condition_id(level, duration),
                        "level": level,
                        "duration": duration,
                        "within_condition_trial": within_condition_trial,
                        "is_catch": False,
                        "catch_type": None,
                        "no_task_position": no_task_position,
                        "task_position": task_position,
                    }
                )

    rounds: list[list[dict[str, Any]]] = []
    for trial_index in range(design.CHOICES_PER_CONDITION):
        round_trials = [
            trial
            for trial in main_trials
            if trial["within_condition_trial"] == trial_index + 1
        ]
        rng.shuffle(round_trials)
        rounds.append(round_trials)

    dispersed: list[dict[str, Any]] = []
    previous_condition = None
    for round_trials in rounds:
        if round_trials and round_trials[0]["condition_id"] == previous_condition:
            round_trials[0], round_trials[1] = round_trials[1], round_trials[0]
        dispersed.extend(round_trials)
        if round_trials:
            previous_condition = round_trials[-1]["condition_id"]

    catches: list[dict[str, Any]] = []
    for catch_type, count in design.CATCH_TRIALS.items():
        for _ in range(count):
            no_task_position, task_position = _option_positions(rng)
            catches.append(
                {
                    "condition_id": f"catch_{catch_type}",
                    "level": 0 if catch_type == "trivial" else None,
                    "duration": 1 if catch_type == "trivial" else None,
                    "within_condition_trial": None,
                    "is_catch": True,
                    "catch_type": catch_type,
                    "no_task_position": no_task_position,
                    "task_position": task_position,
                }
            )

    for catch in catches:
        dispersed.insert(rng.randrange(len(dispersed) + 1), catch)

    for trial_number, trial in enumerate(dispersed, start=1):
        trial["trial_number"] = trial_number
    return dispersed


def initial_offer(rng: random.Random) -> float:
    jitter = rng.uniform(-design.INITIAL_OFFER_JITTER, design.INITIAL_OFFER_JITTER)
    return clamp_offer(design.INITIAL_OFFER + jitter)


def clamp_offer(offer: float) -> float:
    bounded = min(design.ALTERNATIVE_REWARD_MAX, max(design.ALTERNATIVE_REWARD_MIN, offer))
    return round(bounded, 4)


def adjust_offer(current_offer: float, chose_task: bool, step_index: int) -> float:
    """Move the no-task offer toward indifference after a completed choice."""
    step = design.ADAPTIVE_STEPS[min(max(step_index, 0), len(design.ADAPTIVE_STEPS) - 1)]
    direction = 1 if chose_task else -1
    return clamp_offer(current_offer + direction * step)


def catch_trial_rewards(catch_type: str) -> tuple[float, float, int | None, int | None]:
    """Return no-task reward, task reward, level, duration for a control trial."""
    if catch_type == "dominated":
        return design.TASK_REWARD, design.TASK_REWARD, None, None
    if catch_type == "trivial":
        return 0.0, design.TASK_REWARD, 0, 1
    raise ValueError(f"Unknown catch trial type: {catch_type}")


def implemented_outcome(choice: dict[str, Any]) -> dict[str, Any]:
    """Resolve a chosen main trial without changing the participant's choice."""
    if choice.get("is_catch"):
        raise ValueError("Catch trials cannot determine the implemented outcome")
    chosen_option = choice.get("chosen_option")
    if chosen_option not in ("task", "no_task"):
        raise ValueError("A completed main choice is required")
    return {
        "trial_number": choice["trial_number"],
        "condition_id": choice["condition_id"],
        "chosen_option": chosen_option,
        "task_selected": chosen_option == "task",
        "credits_awarded": (
            float(choice["task_reward"])
            if chosen_option == "task"
            else float(choice["alternative_reward"])
        ),
        "level": choice["level"],
        "duration": choice["duration"],
    }