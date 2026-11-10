import random
import unittest
from collections import Counter

from app import experiment_design as design
from app.experiment import (
    adjust_offer,
    build_trial_schedule,
    implemented_outcome,
    initial_offer,
)


class ExperimentScheduleTests(unittest.TestCase):
    def setUp(self):
        self.schedule = build_trial_schedule(random.Random(41))

    def test_schedule_has_sixteen_conditions_five_choices_each_and_six_catches(self):
        main_trials = [trial for trial in self.schedule if not trial["is_catch"]]
        catches = [trial for trial in self.schedule if trial["is_catch"]]
        counts = Counter(trial["condition_id"] for trial in main_trials)
        expected_conditions = len(design.ENABLED_N_BACK_LEVELS) * len(design.DURATIONS_MINUTES)

        self.assertEqual(len(counts), expected_conditions)
        self.assertEqual(set(counts.values()), {design.CHOICES_PER_CONDITION})
        self.assertEqual(len(main_trials), 80)
        self.assertEqual(len(catches), 6)
        self.assertEqual(len(self.schedule), 86)
        self.assertEqual(Counter(trial["catch_type"] for trial in catches), {"dominated": 3, "trivial": 3})

    def test_conditions_are_interleaved_and_positions_are_opposed(self):
        main_trials = [trial for trial in self.schedule if not trial["is_catch"]]
        longest_run = 1
        run_length = 1
        for previous, current in zip(main_trials, main_trials[1:]):
            run_length = run_length + 1 if current["condition_id"] == previous["condition_id"] else 1
            longest_run = max(longest_run, run_length)
        self.assertLessEqual(longest_run, 1)
        self.assertTrue(all(trial["no_task_position"] != trial["task_position"] for trial in self.schedule))
        self.assertGreater(len({trial["task_position"] for trial in self.schedule}), 1)

    def test_offers_are_bounded_and_staircase_moves_in_expected_direction(self):
        offer = initial_offer(random.Random(10))
        self.assertGreaterEqual(offer, design.ALTERNATIVE_REWARD_MIN)
        self.assertLessEqual(offer, design.ALTERNATIVE_REWARD_MAX)
        self.assertGreater(adjust_offer(5.0, chose_task=True, step_index=0), 5.0)
        self.assertLess(adjust_offer(5.0, chose_task=False, step_index=0), 5.0)
        self.assertEqual(adjust_offer(9.5, chose_task=True, step_index=0), 9.5)
        self.assertEqual(adjust_offer(0.5, chose_task=False, step_index=0), 0.5)

    def test_implemented_outcome_respects_original_choice_and_excludes_catches(self):
        choice = {
            "trial_number": 7,
            "condition_id": "E2_T4",
            "chosen_option": "no_task",
            "task_reward": 10.0,
            "alternative_reward": 6.25,
            "level": 2,
            "duration": 4,
            "is_catch": False,
        }
        outcome = implemented_outcome(choice)
        self.assertFalse(outcome["task_selected"])
        self.assertEqual(outcome["credits_awarded"], 6.25)
        self.assertEqual(outcome["trial_number"], choice["trial_number"])
        with self.assertRaises(ValueError):
            implemented_outcome({**choice, "is_catch": True})


if __name__ == "__main__":
    unittest.main()