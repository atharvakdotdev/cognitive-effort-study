"""Central, researcher-editable parameters for the experiment."""

DURATIONS_MINUTES = (1, 2, 4, 8)
N_BACK_LEVELS = (0, 1, 2, 3)
ENABLED_N_BACK_LEVELS = (0, 1, 2, 3)
CHOICES_PER_CONDITION = 5
CATCH_TRIALS = {"dominated": 3, "trivial": 3}
TASK_REWARD = 10.0
ALTERNATIVE_REWARD_MIN = 0.5
ALTERNATIVE_REWARD_MAX = 9.5
INITIAL_OFFER = 5.0
INITIAL_OFFER_JITTER = 1.0
ADAPTIVE_STEPS = (2.5, 1.25, 0.625, 0.3125)
PRACTICE_DURATION_SECONDS = 60
STIMULUS_DURATION_MS = 500
INTER_STIMULUS_INTERVAL_MS = 2000
TARGET_PROPORTION = 0.30

QUALITY_THRESHOLDS = {
    "zero_back_min_accuracy": 0.80,
    "catch_failures_max": 1,
    "missing_response_fraction_max": 0.20,
    "repetitive_response_streak": 20,
    "focus_loss_count": 5,
}