"""Pure experiment-design logic, independent of Flask and persistence."""

from __future__ import annotations

import random
import math
from typing import Any

from . import experiment_design as design


def condition_id(reward: int, level: int, duration: int) -> str:
    return f"R{reward}_E{level}_T{duration}"


def legacy_condition_id(level: int, duration: int) -> str:
    return f"E{level}_T{duration}"


def _option_positions(rng: random.Random) -> tuple[str, str]:
    task_position = rng.choice(("left", "right"))
    no_task_position = "right" if task_position == "left" else "left"
    return no_task_position, task_position


def build_trial_schedule(
    rng: random.Random,
    task_rewards: tuple[int, ...] = design.TASK_REWARDS,
    choices_per_condition: int | None = None,
    legacy_condition_ids: bool = False,
) -> list[dict[str, Any]]:
    """Build a dispersed schedule of main and control trials."""
    if choices_per_condition is None:
        choices_per_condition = design.CHOICES_PER_CONDITION
    if choices_per_condition < 1:
        raise ValueError("choices_per_condition must be positive")
    if not task_rewards or any(reward <= 0 for reward in task_rewards):
        raise ValueError("task_rewards must contain positive values")
    main_trials: list[dict[str, Any]] = []
    for reward in task_rewards:
        for level in design.ENABLED_N_BACK_LEVELS:
            for duration in design.DURATIONS_MINUTES:
                key = legacy_condition_id(level, duration) if legacy_condition_ids else condition_id(reward, level, duration)
                for within_condition_trial in range(1, choices_per_condition + 1):
                    no_task_position, task_position = _option_positions(rng)
                    main_trials.append(
                        {
                            "condition_id": key,
                            "reward": reward,
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
    for trial_index in range(choices_per_condition):
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
        if len(round_trials) > 1 and round_trials[0]["condition_id"] == previous_condition:
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


def initial_offer(
    rng: random.Random,
    task_reward: float,
    *,
    minimum: float = design.MIN_ALTERNATIVE_REWARD,
    integer: bool = True,
) -> int | float:
    jitter = rng.uniform(-design.INITIAL_OFFER_JITTER, design.INITIAL_OFFER_JITTER)
    return clamp_offer(
        task_reward * design.INITIAL_OFFER_PROPORTION + jitter,
        task_reward,
        minimum=minimum,
        integer=integer,
    )


def clamp_offer(
    offer: float,
    task_reward: float,
    *,
    minimum: float = design.MIN_ALTERNATIVE_REWARD,
    integer: bool = True,
) -> int | float:
    if integer:
        bounded = min(int(task_reward), max(int(minimum), math.floor(offer + 0.5)))
        return int(bounded)
    bounded = min(task_reward, max(minimum, offer))
    return round(bounded, 4)


def adjust_offer(
    current_offer: float,
    chose_task: bool,
    step_index: int,
    task_reward: float,
    *,
    minimum: float = design.MIN_ALTERNATIVE_REWARD,
    integer: bool = True,
) -> int | float:
    """Move the no-task offer toward indifference after a completed choice."""
    step = design.ADAPTIVE_STEPS[min(max(step_index, 0), len(design.ADAPTIVE_STEPS) - 1)]
    direction = 1 if chose_task else -1
    candidate = current_offer + direction * step
    if integer:
        rounded = math.floor(candidate + 0.5)
        if rounded == int(current_offer):
            rounded += direction
        candidate = rounded
    return clamp_offer(candidate, task_reward, minimum=minimum, integer=integer)


def catch_trial_rewards(catch_type: str) -> tuple[float, float, int | None, int | None]:
    """Return no-task reward, task reward, level, duration for a control trial."""
    if catch_type == "dominated":
        return design.CATCH_TASK_REWARD, design.CATCH_TASK_REWARD, None, None
    if catch_type == "trivial":
        return design.MIN_ALTERNATIVE_REWARD, design.CATCH_TASK_REWARD, 0, 1
    raise ValueError(f"Unknown catch trial type: {catch_type}")


def _whole_credit_value(value: float) -> int | float:
    numeric_value = float(value)
    return int(numeric_value) if numeric_value.is_integer() else numeric_value


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
        "credits_awarded": _whole_credit_value(
            choice["task_reward"] if chosen_option == "task" else choice["alternative_reward"]
        ),
        "task_reward": _whole_credit_value(choice["task_reward"]),
        "level": choice["level"],
        "duration": choice["duration"],
    }