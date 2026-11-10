import csv
import io
import json
import unittest
from datetime import timedelta

from sqlalchemy import text

from app import create_app, db, experiment_design as design, upgrade_database_schema
from app.demo_data import seed_demo_data
from app.models import Choice, ImplementedOutcome, NBackResponse, NBackTask, Participant, Researcher, UserAccount, utc_now
from app.nback import target_flags
from app.participant import _create_choice_schedule, _select_implemented_outcome


class WebWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
        })
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.session.remove()
        db.engine.dispose()
        self.context.pop()

    def make_participant(self, participant_id="P-TEST", is_test=False, stage="choices"):
        participant = Participant(
            participant_id=participant_id,
            random_seed=321,
            consented=True,
            age_eligible=True,
            current_stage=stage,
            is_test=is_test,
        )
        db.session.add(participant)
        db.session.flush()
        if stage == "choices":
            _create_choice_schedule(participant)
        return participant

    def test_additive_schema_upgrade_preserves_and_marks_existing_participant(self):
        participant = Participant(participant_id="P-LEGACY", random_seed=123)
        db.session.add(participant)
        db.session.commit()
        db.session.execute(text("ALTER TABLE participant DROP COLUMN experiment_version"))
        db.session.commit()
        db.session.remove()

        self.assertTrue(upgrade_database_schema())
        db.session.remove()
        record = db.session.execute(text(
            "SELECT participant_id, random_seed, experiment_version "
            "FROM participant WHERE participant_id = 'P-LEGACY'"
        )).one()
        self.assertEqual(tuple(record), ("P-LEGACY", 123, "fixed_reward_v1"))
        self.assertFalse(upgrade_database_schema())

    def test_existing_fixed_reward_participant_keeps_legacy_schedule(self):
        participant = Participant(
            participant_id="P-OLD-DESIGN",
            random_seed=123,
            experiment_version=design.LEGACY_EXPERIMENT_VERSION,
            current_stage="practice_rating",
        )
        db.session.add(participant)
        db.session.flush()
        _create_choice_schedule(participant)
        choices = Choice.query.filter_by(participant_id=participant.participant_id).all()
        main = [choice for choice in choices if not choice.is_catch]
        self.assertEqual(len(main), 80)
        self.assertEqual(len(choices), 86)
        self.assertEqual({choice.task_reward for choice in choices}, {10.0})
        self.assertTrue(all(choice.condition_id.startswith("E") for choice in main))

    def test_demo_data_uses_all_reward_levels_and_reward_bounded_offers(self):
        self.assertEqual(seed_demo_data(), 3)
        choices = Choice.query.join(Participant).filter(Participant.is_test.is_(True)).all()
        self.assertEqual(len(choices), 3 * 246)
        main = [choice for choice in choices if not choice.is_catch]
        self.assertEqual({choice.task_reward for choice in main}, set(design.TASK_REWARDS))
        self.assertTrue(all(0 <= choice.alternative_reward <= choice.task_reward for choice in main))

    def test_underage_consent_is_recorded_and_terminated(self):
        welcome = self.client.get("/")
        self.assertIn(b"246", welcome.data)
        self.assertNotIn("25–30 min".encode(), welcome.data)
        response = self.client.post("/consent", data={
            "age_confirmed": "no", "read_confirmed": "yes", "agree_confirmed": "yes"
        })
        self.assertEqual(response.status_code, 200)
        participant = Participant.query.one()
        self.assertFalse(participant.age_eligible)
        self.assertEqual(participant.status, "excluded")
        with self.client.session_transaction() as browser_session:
            self.assertNotIn("participant_id", browser_session)

    def test_active_participant_session_persists_across_refresh(self):
        response = self.client.post("/consent", data={
            "age_confirmed": "yes", "read_confirmed": "yes", "agree_confirmed": "yes"
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertTrue(browser_session.permanent)
            participant_id = browser_session["participant_id"]
        resumed = self.client.get("/resume")
        self.assertEqual(resumed.status_code, 302)
        self.assertIn("/instructions", resumed.location)
        self.assertEqual(db.session.get(Participant, participant_id).current_stage, "instructions")

    def test_test_mode_sessions_are_marked_and_visibly_labelled(self):
        self.app.config["EXPERIMENT_TEST_MODE"] = True
        response = self.client.post("/consent", data={
            "age_confirmed": "yes", "read_confirmed": "yes", "agree_confirmed": "yes"
        }, follow_redirects=True)
        participant = Participant.query.one()
        self.assertTrue(participant.is_test)
        self.assertTrue(participant.participant_id.startswith("TEST-"))
        self.assertIn(b"TEST MODE", response.data)

    def test_withdrawal_removes_collected_choice_records(self):
        participant = self.make_participant(participant_id="P-WITHDRAW")
        db.session.commit()
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = participant.participant_id
        self.assertEqual(Choice.query.filter_by(participant_id=participant.participant_id).count(), 246)
        self.assertEqual(self.client.post("/withdraw").status_code, 200)
        self.assertEqual(Choice.query.filter_by(participant_id=participant.participant_id).count(), 0)
        self.assertEqual(db.session.get(Participant, participant.participant_id).status, "withdrawn")

    def test_participant_cannot_skip_to_choices_and_cannot_repeat_a_trial(self):
        self.assertEqual(self.client.get("/choice").status_code, 302)
        participant = self.make_participant()
        db.session.commit()
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = participant.participant_id
        choice_page = self.client.get("/choice")
        self.assertEqual(choice_page.status_code, 200)
        self.assertIn(b'id="chosen-option" type="hidden" name="chosen_option"', choice_page.data)
        first = Choice.query.filter_by(participant_id=participant.participant_id).order_by(Choice.trial_number).first()
        response = self.client.post(f"/choice/{first.trial_number}", data={
            "chosen_option": "no_task", "response_time_ms": "1200"
        })
        self.assertEqual(response.status_code, 302)
        duplicate = self.client.post(f"/choice/{first.trial_number}", data={
            "chosen_option": "task", "response_time_ms": "800"
        })
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(Choice.query.filter_by(
            participant_id=participant.participant_id, trial_number=first.trial_number
        ).one().chosen_option, "no_task")

    def test_choice_endpoint_updates_each_condition_offer_in_both_directions(self):
        for participant_id, selected_option in (("P-UP", "task"), ("P-DOWN", "no_task")):
            participant = self.make_participant(participant_id=participant_id)
            db.session.commit()
            with self.client.session_transaction() as browser_session:
                browser_session["participant_id"] = participant.participant_id
            schedule = Choice.query.filter_by(participant_id=participant.participant_id).order_by(Choice.trial_number).all()
            first_main = next(choice for choice in schedule if not choice.is_catch)
            for choice in schedule:
                if choice.trial_number > first_main.trial_number:
                    break
                selection = selected_option if choice.id == first_main.id else (
                    "no_task" if choice.catch_type == "dominated" else "task"
                )
                response = self.client.post(f"/choice/{choice.trial_number}", data={
                    "chosen_option": selection, "response_time_ms": "900"
                })
                self.assertEqual(response.status_code, 302)
            next_same_condition = Choice.query.filter_by(
                participant_id=participant.participant_id,
                condition_id=first_main.condition_id,
                is_catch=False,
            ).filter(Choice.trial_number > first_main.trial_number).order_by(Choice.trial_number).first()
            self.assertIsNotNone(next_same_condition)
            if selected_option == "task":
                self.assertGreater(next_same_condition.alternative_reward, first_main.alternative_reward)
            else:
                self.assertLess(next_same_condition.alternative_reward, first_main.alternative_reward)

    def test_staircases_are_independent_persisted_and_ignore_catch_trials(self):
        participant = self.make_participant(participant_id="P-STAIRCASE")
        db.session.commit()
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = participant.participant_id
        schedule = Choice.query.filter_by(participant_id=participant.participant_id).order_by(Choice.trial_number).all()
        first_main = next(choice for choice in schedule if not choice.is_catch)

        for choice in schedule:
            if choice.trial_number >= first_main.trial_number:
                break
            before = db.session.get(Participant, participant.participant_id).adaptive_state
            self.assertTrue(choice.is_catch)
            response = self.client.post(f"/choice/{choice.trial_number}", data={
                "chosen_option": "no_task", "response_time_ms": "900"
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(db.session.get(Participant, participant.participant_id).adaptive_state, before)

        same_dimensions_other_reward = Choice.query.filter(
            Choice.participant_id == participant.participant_id,
            Choice.is_catch.is_(False),
            Choice.n_back_level == first_main.n_back_level,
            Choice.duration_minutes == first_main.duration_minutes,
            Choice.task_reward != first_main.task_reward,
        ).order_by(Choice.task_reward).first()
        self.assertIsNotNone(same_dimensions_other_reward)
        state_before = json.loads(participant.adaptive_state)
        other_state_before = state_before[same_dimensions_other_reward.condition_id].copy()

        shown = self.client.get("/choice")
        self.assertEqual(shown.status_code, 200)
        self.assertIn(f"{first_main.task_reward:.2f}".encode(), shown.data)
        self.client.post(f"/choice/{first_main.trial_number}", data={
            "chosen_option": "task", "response_time_ms": "900"
        })
        state_after = json.loads(db.session.get(Participant, participant.participant_id).adaptive_state)
        condition_state = state_after[first_main.condition_id]
        self.assertEqual(condition_state["previous_choice"], "task")
        self.assertEqual(condition_state["step_index"], 1)
        self.assertEqual(condition_state["offers_presented"], 1)
        self.assertEqual(state_after[same_dimensions_other_reward.condition_id], other_state_before)

        next_same_condition = Choice.query.filter_by(
            participant_id=participant.participant_id,
            condition_id=first_main.condition_id,
            is_catch=False,
        ).filter(Choice.trial_number > first_main.trial_number).order_by(Choice.trial_number).first()
        self.assertGreater(next_same_condition.alternative_reward, first_main.alternative_reward)
        self.assertEqual(condition_state["current_x"], next_same_condition.alternative_reward)

        between = Choice.query.filter(
            Choice.participant_id == participant.participant_id,
            Choice.trial_number > first_main.trial_number,
            Choice.trial_number < next_same_condition.trial_number,
        ).order_by(Choice.trial_number).all()
        for choice in between:
            self.assertEqual(self.client.get("/choice").status_code, 200)
            response = self.client.post(f"/choice/{choice.trial_number}", data={
                "chosen_option": "no_task", "response_time_ms": "900"
            })
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/choice").status_code, 200)
        state_after_interleaving = json.loads(db.session.get(Participant, participant.participant_id).adaptive_state)
        self.assertEqual(state_after_interleaving[first_main.condition_id]["current_x"], next_same_condition.alternative_reward)
        self.assertEqual(state_after_interleaving[first_main.condition_id]["offers_presented"], 2)

    def test_only_one_completed_main_choice_is_implemented_and_choice_is_preserved(self):
        participant = self.make_participant(participant_id="P-OUTCOME")
        choices = Choice.query.filter_by(participant_id=participant.participant_id).all()
        for choice in choices:
            if choice.is_catch:
                choice.chosen_option = "no_task" if choice.catch_type == "dominated" else "task"
            else:
                choice.alternative_reward = choice.alternative_reward or 5.0
                choice.chosen_option = "task" if choice.trial_number % 2 else "no_task"
            choice.task_selected = choice.chosen_option == "task"
        db.session.commit()
        _select_implemented_outcome(participant)
        first = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).one()
        self.assertFalse(first.choice.is_catch)
        self.assertEqual(first.chosen_option, first.choice.chosen_option)
        expected_credits = 0.0 if first.task_selected else first.choice.alternative_reward
        self.assertEqual(first.credits_awarded, expected_credits)
        _select_implemented_outcome(participant)
        self.assertEqual(ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).count(), 1)

    def test_selected_task_credits_are_recorded_only_after_task_completion(self):
        participant = self.make_participant(participant_id="P-IMPLEMENTED")
        choices = Choice.query.filter_by(participant_id=participant.participant_id).all()
        for choice in choices:
            choice.chosen_option = "no_task" if choice.is_catch and choice.catch_type == "dominated" else "task"
            if not choice.is_catch:
                choice.alternative_reward = choice.alternative_reward or 5.0
            choice.task_selected = choice.chosen_option == "task"
        db.session.commit()
        _select_implemented_outcome(participant)
        outcome = ImplementedOutcome.query.filter_by(participant_id=participant.participant_id).one()
        self.assertTrue(outcome.task_selected)
        self.assertEqual(outcome.credits_awarded, 0.0)
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = participant.participant_id
        self.client.post("/outcome/continue")
        self.assertEqual(self.client.get("/outcome/task").status_code, 200)
        task = NBackTask.query.filter_by(participant_id=participant.participant_id, run_type="outcome").one()
        task.started_at = utc_now() - timedelta(seconds=outcome.choice.duration_minutes * 60 + 2)
        sequence = json.loads(task.sequence_json)
        flags = target_flags(task.task_level, sequence)
        responses = [
            {"response": "target" if target else "non_target", "reaction_time_ms": 450}
            for target in flags
        ]
        db.session.commit()
        result = self.client.post("/outcome/task/complete", json={"responses": responses})
        self.assertEqual(result.status_code, 200)
        db.session.refresh(outcome)
        self.assertTrue(outcome.task_completed)
        self.assertEqual(outcome.credits_awarded, outcome.choice.task_reward)

    def test_practice_task_saves_server_scored_responses_after_full_duration(self):
        participant = self.make_participant(participant_id="P-PRACTICE", stage="practice")
        participant.practice_order = "[0, 1, 2, 3]"
        db.session.commit()
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = participant.participant_id
        self.assertEqual(self.client.get("/practice/0").status_code, 200)
        task = NBackTask.query.filter_by(participant_id=participant.participant_id, task_level=0, run_type="practice").one()
        task.started_at = utc_now() - timedelta(seconds=62)
        sequence = json.loads(task.sequence_json)
        flags = target_flags(0, sequence)
        responses = [
            {"response": "target" if target else "non_target", "reaction_time_ms": 450}
            for target in flags
        ]
        db.session.commit()
        result = self.client.post("/practice/0/complete", json={"responses": responses})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(NBackResponse.query.filter_by(task_run_id=task.id).count(), len(sequence))
        self.assertTrue(all(response.accuracy for response in NBackResponse.query.filter_by(task_run_id=task.id).all()))
        self.assertEqual(db.session.get(Participant, participant.participant_id).current_stage, "practice_rating")

    def test_researcher_routes_require_authentication(self):
        response = self.client.get("/researcher/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/researcher/login", response.location)
        self.assertEqual(self.client.get("/researcher/login").status_code, 200)

    def test_user_account_resumes_its_session_and_cannot_open_researcher_tools(self):
        self.assertEqual(self.client.get("/account/register").status_code, 200)
        self.assertEqual(self.client.get("/account/login").status_code, 200)
        response = self.client.post("/account/register", data={
            "username": "study_user",
            "password": "a-long-test-password",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/researcher/").status_code, 403)
        self.client.post("/consent", data={
            "age_confirmed": "yes", "read_confirmed": "yes", "agree_confirmed": "yes"
        })
        account = UserAccount.query.one()
        participant = db.session.get(Participant, account.participant_id)
        self.assertIsNotNone(participant)
        self.assertFalse(participant.is_test)
        self.client.post("/account/logout")
        response = self.client.post("/account/login", data={
            "username": "study_user",
            "password": "a-long-test-password",
        })
        self.assertEqual(response.status_code, 302)
        resumed = self.client.get("/resume")
        self.assertIn("/instructions", resumed.location)

    def test_researcher_debug_jumps_only_through_synthetic_sessions(self):
        researcher = Researcher(username="debugger")
        researcher.set_password("not-a-production-password")
        db.session.add(researcher)
        db.session.commit()
        self.client.post("/researcher/login", data={
            "username": "debugger", "password": "not-a-production-password"
        })
        self.assertEqual(self.client.get("/researcher/debug").status_code, 200)
        response = self.client.post("/researcher/debug/start", data={"stage": "choices"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as browser_session:
            participant_id = browser_session["participant_id"]
            self.assertTrue(browser_session["researcher_debug"])
        participant = db.session.get(Participant, participant_id)
        self.assertTrue(participant.is_test)
        detail = self.client.get(f"/researcher/participants/{participant_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Raw N-back responses", detail.data)
        step_pages = {
            "consent": "/consent",
            "instructions": "/instructions",
            "quiz": "/quiz",
            "practice": "/practice/0",
            "practice_rating": "/practice-rating/0",
            "choices": "/choice",
            "outcome": "/outcome",
            "outcome_task": "/outcome/task",
            "final_rating": "/final-rating",
            "debrief": "/debrief",
            "complete": "/debrief",
        }
        for stage, page in step_pages.items():
            with self.subTest(stage=stage):
                self.client.post("/researcher/debug/jump", data={"stage": stage})
                self.assertEqual(self.client.get(page).status_code, 200)
        real = self.make_participant(participant_id="P-REAL-DEBUG", stage="choices")
        db.session.commit()
        with self.client.session_transaction() as browser_session:
            browser_session["participant_id"] = real.participant_id
            browser_session["researcher_debug"] = True
        self.assertEqual(
            self.client.post("/researcher/debug/jump", data={"stage": "quiz"}).status_code,
            403,
        )
        self.assertEqual(self.client.get("/researcher/participants/P-NOT-REAL").status_code, 404)

    def test_login_and_csv_exports_include_expected_columns_and_exclude_synthetic(self):
        real = Participant(participant_id="P-REAL", random_seed=1, is_test=False, current_stage="choices")
        synthetic = Participant(participant_id="SYNTHETIC-001", random_seed=2, is_test=True, current_stage="complete")
        db.session.add_all((real, synthetic))
        db.session.flush()
        for participant, trial in ((real, 1), (synthetic, 2)):
            db.session.add(Choice(
                participant_id=participant.participant_id,
                trial_number=trial,
                condition_id="E1_T2",
                n_back_level=1,
                duration_minutes=2,
                alternative_reward=5.0,
                task_reward=10.0,
                chosen_option="task",
                task_selected=True,
                no_task_position="left",
                task_position="right",
                is_catch=False,
            ))
        researcher = Researcher(username="researcher")
        researcher.set_password("not-a-production-password")
        db.session.add(researcher)
        db.session.commit()
        self.client.post("/researcher/login", data={"username": "researcher", "password": "not-a-production-password"})
        self.assertEqual(self.client.get("/researcher/analysis").status_code, 200)

        exports = {
            "/researcher/export/participants.csv": ("session_creation_time", "task_reward_r"),
            "/researcher/export/choices.csv": ("alternative_reward_x", "task_reward_r"),
            "/researcher/export/nback-responses.csv": ("correct_response_target", "task_reward_r"),
            "/researcher/export/effort-ratings.csv": ("rating_0_100", "task_reward_r"),
            "/researcher/export/implemented-outcomes.csv": ("selected_trial_number", "task_reward_r"),
        }
        for path, expected_columns in exports.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            header = next(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
            self.assertIn("participant_id", header)
            for expected_column in expected_columns:
                self.assertIn(expected_column, header)
        exported_choices = self.client.get("/researcher/export/choices.csv").get_data(as_text=True)
        self.assertIn("P-REAL", exported_choices)
        self.assertNotIn("SYNTHETIC-001", exported_choices)


if __name__ == "__main__":
    unittest.main()