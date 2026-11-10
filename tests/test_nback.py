import random
import unittest

from app.nback import generate_sequence, target_flags, validate_responses


class NBackTests(unittest.TestCase):
    def test_sequence_uses_consonants_and_targets_follow_each_level(self):
        for level in range(4):
            sequence = generate_sequence(level, 200, random.Random(level + 12))
            self.assertTrue(all(letter in "BCDFGHJKLMNPQRSTVWXZ" for letter in sequence))
            flags = target_flags(level, sequence)
            for index, is_target in enumerate(flags):
                expected = sequence[index] == "X" if level == 0 else index >= level and sequence[index] == sequence[index - level]
                self.assertEqual(is_target, expected)
            self.assertGreater(sum(flags), 0)

    def test_responses_are_validated_and_accuracy_is_derived(self):
        sequence = ["B", "C", "C"]
        results = validate_responses(
            1,
            sequence,
            [
                {"response": "non_target", "reaction_time_ms": 450},
                {"response": "target", "reaction_time_ms": 300},
                {"response": "target", "reaction_time_ms": 250},
            ],
        )
        self.assertEqual([item["accuracy"] for item in results], [True, False, True])
        self.assertTrue(results[2]["correct_response"])

    def test_invalid_length_response_and_reaction_time_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_responses(0, ["B"], [])
        with self.assertRaises(ValueError):
            validate_responses(0, ["B"], [{"response": "target", "reaction_time_ms": 5000}])


if __name__ == "__main__":
    unittest.main()