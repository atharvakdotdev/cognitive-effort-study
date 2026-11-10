# Cognitive Effort and Time Discounting Study

A server-rendered Flask research instrument for collecting trial-level choices about cognitive-task effort and duration. It uses SQLite and traditional WSGI hosting; no Node.js, background worker, WebSocket, or Docker service is needed.

## Research design

The main design crosses four N-back levels (0-, 1-, 2-, and 3-back) with four task durations (1, 2, 4, and 8 minutes). Each of the 16 conditions has five adaptive choices, for 80 main trials. Six interleaved checks bring the session total to 86 choices. Each main choice compares a variable no-task reward with 10 credits after the displayed cognitive task. One of the 80 main choices, never a catch trial, is randomly selected for implementation.

The no-task offer starts near 5 credits with up to 1 credit of jitter. Choosing the task raises the next offer for that condition; choosing no task lowers it. Step sizes are 2.5, 1.25, 0.625, and 0.3125 credits. All offers are bounded between 0.5 and 9.5 credits. Trial-level choices are stored separately; the final staircase offer is not treated as an SV estimate.

The task is a browser-based letter N-back. Space means a match and F means no match. Letters are shown for 500 ms, followed by 2,000 ms blank. Practice is 60 seconds at each enabled level in randomized order. Sequence generation and response scoring are server-validated. The selected outcome task uses the exact level and duration from the original choice.

The reward is fixed at $R=10$ credits. Thus, the experiment estimates how duration $T$ and operational effort $E$ affect subjective value at this reward level; it does not identify a complete reward-dependent function $SV(R,T,E)$. N-back level is an operational measure of demand, not a perfect direct measure of cognitive effort. Subjective effort ratings provide a separate measure.

### Candidate subjective-value models

The researcher analysis page and exports support later trial-level modelling. The application does not fit these models.

1. **M1 — Additive:** $SV = R - aT - bE$
2. **M2 — Additive with interaction:** $SV = R - aT - bE - cTE$
3. **M3 — Effort-weighted time:** $SV = R - T^q(a + bE^p)$
4. **M4 — Hyperbolic:** $SV = R / (1 + aT + bE + cTE)$
5. **M5 — Exponential:** $SV = R \times \exp[-(aT^q + bE^p + cT^qE^p)]$

Here $a,b,c,p,q$ are model parameters. Model comparison and inference should be done using the exported trial-level data in an appropriate Python or R analysis; the descriptive dashboard is not evidence that one equation is best.

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

Run the local server with `python run.py`, then open `http://127.0.0.1:5000/`. Researcher tools are at `/researcher/login`. Participants can optionally create a pseudonymous user account at `/account/register`; the account is linked to that user's participant code to support resuming, while usernames are excluded from research exports. The existing anonymous participation path remains available. The default SQLite file is `instance/experiment.sqlite3`. Set `DATABASE_URL` to an absolute SQLite URI to select another persistent location. Use a persistent `SECRET_KEY` across restarts; set `COOKIE_SECURE=1` when serving over HTTPS.

Researchers can use **Survey debug** from the dashboard to create a synthetic test session at any survey step. While in that session, the debug toolbar can jump between steps and open the detailed diagnostic record. These sessions are marked `is_test=true` and excluded from research exports and descriptive analysis. Researcher credentials remain separate from participant user accounts.

After deploying this update to an existing database, run `flask --app run.py init-db` once to create the new user-account table. This is additive and does not alter existing participant tables.

Create labelled synthetic dashboard examples with `flask --app run.py seed-demo`. They have `is_test=true`, are visibly labelled, and are excluded from all five real research exports. The seed command is idempotent and does not create genuine participant records.

For a technical walkthrough of the participant flow, set `EXPERIMENT_TEST_MODE=1` in the environment before starting the app. Participant sessions created in this mode receive a `TEST-` ID, show a persistent test-mode banner, and are excluded from research exports and descriptive analysis. Remove the setting or set it to `0` before genuine recruitment. This is separate from the `seed-demo` command, which creates synthetic dashboard records.

## Tests

Run the automated suite with:

```bash
python -m unittest discover -s tests -v
```

The tests cover the 16-condition/86-choice schedule, interleaving and positions, staircase bounds/direction, N-back sequence and response validation, implementation outcome rules, consent eligibility, state/duplicate-choice protection, researcher authentication, and CSV columns and synthetic-data exclusion.

## Data quality and analysis

Checks are recorded as participant quality flags and do not delete data or automatically exclude a participant. The current thresholds are centralized in `app/experiment_design.py`:

- 0-back practice accuracy below 80%.
- More than one failed catch choice out of six (dominated checks should be no-task; trivial 0-back checks should be task).
- More than 20% missing N-back responses.
- A repeated identical response streak of at least 20.
- Five or more focus-loss events during choice/outcome-task stages.
- `incomplete_experiment` is present during an active session and cleared on completion.

Age ineligibility and a second failed comprehension attempt end the session with a recorded reason. A researcher can mark another session excluded from its detail page; original records and flags remain inspectable and included in descriptive summaries and real exports. Synthetic data is the only data filtered from research exports/analysis.

Researcher downloads are separate files: `participants.csv`, `choices.csv`, `nback_responses.csv`, `effort_ratings.csv`, and `implemented_outcomes.csv`. Participant IDs are preserved across files. The analysis page reports choice acceptance by condition, duration, and effort level; practice ratings; N-back accuracy/reaction time; and completion/exclusion counts. It does not fit or rank SV models.

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

   os.environ.setdefault("SECRET_KEY", "replace-with-a-long-random-secret")
   os.environ.setdefault(
       "DATABASE_URL",
       "sqlite:////home/yourusername/path/to/survey/instance/experiment.sqlite3",
   )
   os.environ.setdefault("COOKIE_SECURE", "1")

   from app import app as application
   ```

   Do not add `app.run()` to the WSGI file. It imports the Flask object and uses the persistent database under the project `instance/` folder.
6. Configure a long random `SECRET_KEY` and the absolute `DATABASE_URL` in the WSGI environment. Keep the same values for the command-line initialization below. Do not commit credentials or secrets.
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

   The password is stored as a Werkzeug password hash. `init-db` creates tables and creates the researcher account only if that username does not already exist.
8. Confirm the database path points to the project `instance/experiment.sqlite3`, not `/tmp` or another ephemeral directory. Ensure the `instance/` directory is writable by the web app.
9. Click **Reload** in the PythonAnywhere Web tab.
10. Verify the participant welcome page at `/` and researcher sign-in at `/researcher/login`. Create a test session and verify its saved records from the authenticated dashboard before recruitment.

PythonAnywhere WSGI imports `app` from the project package; `run.py` only starts a local server behind its main guard. No background process is required. Back up the SQLite file using the account's normal persistent-storage process before and during data collection.

## Before recruitment

The research team must add its real contact details and any required ethics approval information to the participant materials, define how credits convert to bonus payment, and verify those arrangements on the deployed site. The application does not claim ethics approval. Researchers should run a technical pilot, confirm performance and timing on the target browsers, and review whether 3-back performance is adequate; `ENABLED_N_BACK_LEVELS` can be edited in `app/experiment_design.py` if a level must be disabled for a pilot iteration.

One design choice requires particular attention: participants whose randomly implemented choice is no-task are asked for an **expected** mental-demand rating for the selected task, stored as `rating_type=expected`. Participants who complete the selected task receive a `post_task` rating. These are distinct measures and should not be pooled as though both were ratings of experienced task demand.