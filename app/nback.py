"""N-back sequence generation and server-side response validation."""

import random
from typing import Any

from . import experiment_design as design

CONSONANTS = "BCDFGHJKLMNPQRSTVWXZ"


def generate_sequence(level: int, trial_count: int, rng: random.Random) -> list[str]:
    if level not in design.N_BACK_LEVELS:
        raise ValueError("Unsupported N-back level")
    if trial_count < 1:
        raise ValueError("A task sequence must contain at least one stimulus")

    sequence: list[str] = []
    for index in range(trial_count):
        if level == 0:
            is_target = rng.random() < design.TARGET_PROPORTION
            choices = "X" if is_target else CONSONANTS.replace("X", "")
        elif index >= level and rng.random() < design.TARGET_PROPORTION:
            choices = sequence[index - level]
        else:
            previous_letter = sequence[index - level] if index >= level else None
            choices = "".join(letter for letter in CONSONANTS if letter != previous_letter)
        sequence.append(rng.choice(choices))
    return sequence


def target_flags(level: int, sequence: list[str]) -> list[bool]:
    if level == 0:
        return [letter == "X" for letter in sequence]
    return [index >= level and letter == sequence[index - level] for index, letter in enumerate(sequence)]


def validate_responses(
    level: int, sequence: list[str], responses: Any
) -> list[dict[str, Any]]:
    if not isinstance(responses, list) or len(responses) != len(sequence):
        raise ValueError("The response count does not match the assigned sequence")

    flags = target_flags(level, sequence)
    validated: list[dict[str, Any]] = []
    for index, (stimulus, target, response) in enumerate(zip(sequence, flags, responses)):
        if not isinstance(response, dict):
            raise ValueError("Malformed task response")
        actual_response = response.get("response")
        reaction_time = response.get("reaction_time_ms")
        if actual_response not in (None, "target", "non_target"):
            raise ValueError("Unrecognised keyboard response")
        if actual_response is None:
            if reaction_time is not None:
                raise ValueError("A missed response cannot have a reaction time")
        elif (
            isinstance(reaction_time, bool)
            or not isinstance(reaction_time, (int, float))
            or not 0 <= reaction_time <= design.STIMULUS_DURATION_MS + design.INTER_STIMULUS_INTERVAL_MS
        ):
            raise ValueError("Reaction time is outside the trial interval")

        expected_response = "target" if target else "non_target"
        validated.append(
            {
                "trial_index": index + 1,
                "stimulus": stimulus,
                "correct_response": target,
                "actual_response": actual_response,
                "accuracy": actual_response == expected_response,
                "reaction_time_ms": float(reaction_time) if reaction_time is not None else None,
            }
        )
    return validated