# Cognitive Effort and Time Discounting Study

A server-rendered Flask research instrument for collecting trial-level choices about cognitive-task effort and duration. It uses SQLite and traditional WSGI hosting; no Node.js, background worker, WebSocket, or Docker service is needed.

## Research design

The main design crosses three task rewards (5, 10, and 20 credits), four N-back levels (0-, 1-, 2-, and 3-back), and four task durations (1, 2, 4, and 8 minutes), for 48 conditions. Each condition has `CHOICES_PER_CONDITION=5` adaptive choices (researcher-editable in `app/experiment_design.py`), for 240 main choices. Six interleaved checks bring the session total to 246 choices. Each main choice compares a variable no-task reward X with the condition's fixed task reward R. One of the 240 main choices, never a catch trial, is randomly selected for implementation.

Each condition has an independent staircase keyed by `R{reward}_E{level}_T{duration}`. Its first no-task offer starts at half of R with up to 1 credit of jitter, rounded to an integer. Choosing the task raises the next X for that same condition; choosing no task lowers it. The configured step sizes are 2.5, 1.25, 0.625, and 0.3125 credits, rounded to integer offers with at least a 1-credit move when not at a boundary. New and pending offers are integers bounded between 1 and that condition's R. The participant's `adaptive_state` records current X, next step, previous choice, and offers presented for each condition. Trial-level choices are stored separately; the final staircase offer is not treated as an SV estimate. Completed legacy fractional responses are preserved; unchosen legacy offers are rounded and bounded during the schema upgrade.

The task is a browser-based letter N-back. Space means a match and F means no match. Letters are shown for 500 ms, followed by 2,000 ms blank. Practice is 60 seconds at each enabled level in randomized order. Sequence generation and response scoring are server-validated. The selected outcome task uses the exact level and duration from the original choice.

Varying R permits investigation of whether subjective value scales proportionally with reward or whether reward interacts with duration T and operational effort E. N-back level is an operational measure of demand, not a perfect direct measure of cognitive effort. Subjective effort ratings provide a separate measure.

### Candidate subjective-value models

The researcher analysis page and exports support later trial-level modelling. The application does not fit these models.

1. **M1 — Additive:** $SV = R - aT - bE$
2. **M2 — Additive with interaction:** $SV = R - aT - bE - cTE$
3. **M3 — Multiplicative discounting:** $SV = R \times \exp[-(aT^q + bE^p + cT^qE^p)]$
4. **M4 — Hyperbolic discounting:** $SV = R / (1 + aT + bE + cTE)$

Here $a,b,c,p,q$ are model parameters. These are candidates, not established equations. Model comparison and inference should use trial-level choice data in an appropriate Python or R analysis; the descriptive dashboard does not select or validate a model.

## Project layout

```text
app/
  __init__.py              Flask factory, extensions, CLI commands, WSGI app
  config.py                Environment and database configuration
  experiment_design.py     Central design values and quality thresholds
  experiment.py            Trial generation, adaptive offers, outcome resolution
  nback.py                 N-back sequence generation and server validation
  data_quality.py          Non-destructive quality checks
  models.py                SQLAlchemy schema
  participant.py           Consent and participant state machine
  admin.py                 Researcher authentication, dashboard, CSV exports
  analysis.py              Descriptive summaries
  demo_data.py             Clearly marked synthetic records
  templates/               Participant and researcher pages
  static/                  Vanilla JavaScript and CSS
instance/                  Persistent SQLite database (created automatically)
tests/                     Protocol, N-back, route, and export tests
requirements.txt
run.py
```

## Local installation

Python 3.10 or later is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export SECRET_KEY="replace-with-a-long-random-secret"
flask --app run.py init-db
```

To create the first researcher account during database initialization, set credentials in the shell without putting the password in command history:

```bash
export ADMIN_USERNAME="researcher"
read -r -s -p "Initial researcher password: " ADMIN_PASSWORD
export ADMIN_PASSWORD
flask --app run.py init-db
unset ADMIN_PASSWORD
```

Run the local server with `python run.py`, then open `http://127.0.0.1:5000/`. Researcher tools are at `/researcher/login`. Participants can optionally create a pseudonymous user account at `/account/register`; the account is linked to that user's participant code to support resuming, while usernames are excluded from research exports. The existing anonymous participation path remains available. The default SQLite file is `instance/experiment.sqlite3`. Set `DATABASE_URL` to an absolute SQLite URI to select another persistent location. Use a persistent `SECRET_KEY` across restarts; set `COOKIE_SECURE=1` when serving over HTTPS. Set `APP_ENV=production` in deployment to require secure cookies.

Researchers can use **Survey debug** from the dashboard to create a synthetic test session at any survey step. While in that session, the debug toolbar can jump between steps and open the detailed diagnostic record. These sessions are marked `is_test=true` and excluded from research exports and descriptive analysis. Researcher credentials remain separate from participant user accounts.

For an existing database, run `.venv/bin/flask --app run.py init-db` before starting the updated app. The command creates missing tables and adds `participant.experiment_version` with an `ALTER TABLE`; it does not rebuild tables or delete participant data. Existing participants are marked `fixed_reward_v1`; their fixed-10 schedule and completed responses remain intact. Unchosen offers are rounded to whole credits and bounded to 1..R. New participants use `variable_reward_v2` and receive the 240-main-choice design. The migration is idempotent and can be run again safely.

Create labelled synthetic dashboard examples with `flask --app run.py seed-demo`. They have `is_test=true`, are visibly labelled, and are excluded from all five real research exports. The seed command is idempotent and does not create genuine participant records.

For a technical walkthrough of the participant flow, set `EXPERIMENT_TEST_MODE=1` in the environment before starting the app. Participant sessions created in this mode receive a `TEST-` ID, show a persistent test-mode banner, and are excluded from research exports and descriptive analysis. Remove the setting or set it to `0` before genuine recruitment. This is separate from the `seed-demo` command, which creates synthetic dashboard records.

## Tests

Run the automated suite with:

```bash
python -m unittest discover -s tests -v
```

The tests cover all 48 R/T/E conditions and 246 choices, configurable condition counts, interleaving, reward-bounded independent staircases, catch isolation, legacy migration/preservation, N-back sequence and response validation, exact outcome reward resolution, researcher authentication, and R columns in every CSV export.

## Data quality and analysis

Checks are recorded as participant quality flags and do not delete data or automatically exclude a participant. The current thresholds are centralized in `app/experiment_design.py`:

- 0-back practice accuracy below 80%.
- More than one failed catch choice out of six (dominated checks should be no-task; trivial 0-back checks should be task).
- More than 20% missing N-back responses.
- A repeated identical response streak of at least 20.
- Five or more focus-loss events during choice/outcome-task stages.
- `incomplete_experiment` is present during an active session and cleared on completion.

Age ineligibility and a second failed comprehension attempt end the session with a recorded reason. A researcher can mark another session excluded from its detail page; original records and flags remain inspectable and included in descriptive summaries and real exports. Synthetic data is the only data filtered from research exports/analysis.

Researcher downloads are separate files: `participants.csv`, `choices.csv`, `nback_responses.csv`, `effort_ratings.csv`, and `implemented_outcomes.csv`. Each export includes a `task_reward_r` column; it is blank for session-level or practice records that do not have a task reward. Participant IDs are preserved across files. The analysis page reports choice acceptance by R/T/E condition and reward, along with pooled duration/effort summaries, practice ratings, N-back accuracy/reaction time, and completion/exclusion counts. It does not fit or rank SV models.

## PythonAnywhere deployment

1. Upload or clone this repository into a directory under your PythonAnywhere home folder. Keep `instance/` inside that project directory; it is the persistent SQLite location.
2. In a PythonAnywhere Bash console, create a virtual environment using a Python version supported by your account, for example:

   ```bash
   cd /home/yourusername/path/to/survey
   python3.11 -m venv .venv
   ```

3. Install dependencies into that environment:

   ```bash
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. In the PythonAnywhere **Web** tab, create a manual Flask web app, select the same Python version, and set its virtualenv path to `/home/yourusername/path/to/survey/.venv`.
5. Edit the WSGI configuration file shown in the Web tab. Replace the placeholder project path and secret; import the WSGI object as `application`:

   ```python
   import os
   import sys

   project_home = "/home/yourusername/path/to/survey"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ.setdefault("APP_ENV", "production")
   os.environ.setdefault("SECRET_KEY", "replace-with-a-long-random-secret")
   os.environ.setdefault(
       "DATABASE_URL",
       "sqlite:////home/yourusername/path/to/survey/instance/experiment.sqlite3",
   )
   os.environ.setdefault("COOKIE_SECURE", "1")

   from app import app as application
   ```

   Do not add `app.run()` to the WSGI file. It imports the Flask object and uses the persistent database under the project `instance/` folder.
6. Configure a long random `SECRET_KEY`, the absolute `DATABASE_URL`, and `COOKIE_SECURE=1` in the WSGI environment. Keep the same database and secret values for the command-line initialization below. Do not commit credentials or secrets.
7. Initialize the schema and researcher account explicitly from a Bash console. Use the same database URI and secret as in WSGI. Enter the password at the prompt rather than in the command line:

   ```bash
   cd /home/yourusername/path/to/survey
   export SECRET_KEY="the-same-long-random-secret-as-the-WSGI-config"
   export DATABASE_URL="sqlite:////home/yourusername/path/to/survey/instance/experiment.sqlite3"
   export ADMIN_USERNAME="researcher"
   read -r -s -p "Initial researcher password: " ADMIN_PASSWORD
   export ADMIN_PASSWORD
   .venv/bin/flask --app run.py init-db
   unset ADMIN_PASSWORD
   ```

   The password is stored as a Werkzeug password hash. `init-db` creates missing tables, including the consent-submission idempotency table, safely adds the experiment-version column to an existing SQLite database without deleting records, and creates the researcher account only if that username does not already exist. Existing participant sessions remain on the fixed-10-credit design.
8. Confirm the database path points to the project `instance/experiment.sqlite3`, not `/tmp` or another ephemeral directory. Ensure the `instance/` directory is writable by the web app.
9. Click **Reload** in the PythonAnywhere Web tab.
10. Verify the participant welcome page at `/` and researcher sign-in at `/researcher/login`. Create a test session and verify its saved records from the authenticated dashboard before recruitment.

PythonAnywhere WSGI imports `app` from the project package; `run.py` only starts a local server behind its main guard. No background process is required. Back up the SQLite file using the account's normal persistent-storage process before and during data collection.

## Before recruitment

The consent page identifies Atharva Dixit as the independent researcher and states that credits are non-monetary experimental points. Confirm the contact details remain current, confirm applicable ethics review requirements before recruitment, and verify the retention/deletion policy can be carried out. The application does not claim ethics approval. The expanded 246-choice design will take longer than the previous 86-choice version; pilot the full session to establish a credible duration estimate and assess fatigue, attrition, and data quality before recruitment, then add that estimate to the consent materials. Researchers should also confirm performance and timing on target browsers and review whether 3-back performance is adequate; `ENABLED_N_BACK_LEVELS` can be edited in `app/experiment_design.py` if a level must be disabled for a pilot iteration.

One design choice requires particular attention: participants whose randomly implemented choice is no-task are asked for an **expected** mental-demand rating for the selected task, stored as `rating_type=expected`. Participants who complete the selected task receive a `post_task` rating. These are distinct measures and should not be pooled as though both were ratings of experienced task demand.