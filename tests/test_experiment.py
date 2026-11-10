import random
import unittest
from collections import Counter
from itertools import product

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

    def test_schedule_has_48_conditions_five_choices_each_and_six_catches(self):
        main_trials = [trial for trial in self.schedule if not trial["is_catch"]]
        catches = [trial for trial in self.schedule if trial["is_catch"]]
        counts = Counter(trial["condition_id"] for trial in main_trials)
        expected_conditions = len(design.TASK_REWARDS) * len(design.ENABLED_N_BACK_LEVELS) * len(design.DURATIONS_MINUTES)

        self.assertEqual(len(counts), expected_conditions)
        self.assertEqual(set(counts.values()), {design.CHOICES_PER_CONDITION})
        self.assertEqual(len(main_trials), 240)
        self.assertEqual(len(catches), 6)
        self.assertEqual(len(self.schedule), 246)
        self.assertEqual(Counter(trial["catch_type"] for trial in catches), {"dominated": 3, "trivial": 3})
        actual_combinations = {
            (trial["reward"], trial["level"], trial["duration"])
            for trial in main_trials
        }
        expected_combinations = set(product(
            design.TASK_REWARDS,
            design.ENABLED_N_BACK_LEVELS,
            design.DURATIONS_MINUTES,
        ))
        self.assertEqual(actual_combinations, expected_combinations)
        self.assertTrue(all(len({trial["reward"] for trial in main_trials if trial["condition_id"] == condition}) == 1 for condition in counts))
        self.assertEqual(set(counts), {f"R{reward}_E{level}_T{duration}" for reward, level, duration in expected_combinations})

    def test_choice_count_is_configurable_and_legacy_schedule_is_available(self):
        shorter = build_trial_schedule(random.Random(14), choices_per_condition=2)
        main = [trial for trial in shorter if not trial["is_catch"]]
        self.assertEqual(len(main), 96)
        self.assertEqual(set(Counter(trial["condition_id"] for trial in main).values()), {2})
        self.assertEqual(len(shorter), 102)

        legacy = build_trial_schedule(
            random.Random(14),
            task_rewards=(design.CATCH_TASK_REWARD,),
            legacy_condition_ids=True,
        )
        legacy_main = [trial for trial in legacy if not trial["is_catch"]]
        self.assertEqual(len(legacy_main), 80)
        self.assertTrue(all(trial["condition_id"].startswith("E") for trial in legacy_main))

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
        for reward in design.TASK_REWARDS:
            offer = initial_offer(random.Random(10), reward)
            self.assertGreaterEqual(offer, 0)
            self.assertLessEqual(offer, reward)
            self.assertLessEqual(abs(offer - reward / 2), design.INITIAL_OFFER_JITTER)
            self.assertGreater(adjust_offer(reward / 2, chose_task=True, step_index=0, task_reward=reward), reward / 2)
            self.assertLess(adjust_offer(reward / 2, chose_task=False, step_index=0, task_reward=reward), reward / 2)
            self.assertEqual(adjust_offer(reward, chose_task=True, step_index=0, task_reward=reward), reward)
            self.assertEqual(adjust_offer(0, chose_task=False, step_index=0, task_reward=reward), 0)

    def test_adaptive_offer_follows_example_and_reverses_after_no_task(self):
        offer = 5.0
        offer = adjust_offer(offer, chose_task=True, step_index=0, task_reward=10)
        self.assertEqual(offer, 7.5)
        offer = adjust_offer(offer, chose_task=True, step_index=1, task_reward=10)
        self.assertEqual(offer, 8.75)
        offer = adjust_offer(offer, chose_task=False, step_index=2, task_reward=10)
        self.assertEqual(offer, 8.125)

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
        self.assertEqual(outcome["task_reward"], 10.0)
        self.assertEqual(outcome["trial_number"], choice["trial_number"])
        with self.assertRaises(ValueError):
            implemented_outcome({**choice, "is_catch": True})
        task_outcome = implemented_outcome({**choice, "chosen_option": "task", "task_reward": 20.0})
        self.assertTrue(task_outcome["task_selected"])
        self.assertEqual(task_outcome["credits_awarded"], 20.0)


if __name__ == "__main__":
    unittest.main()