(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  if (document.body.dataset.trackFocus === 'true') {
    let visibilityWasHidden = false;
    const record = (eventType) => {
      fetch('/events/focus', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ event_type: eventType }),
        keepalive: true,
      }).catch(() => {});
    };
    document.addEventListener('visibilitychange', () => {
      const hidden = document.visibilityState === 'hidden';
      if (hidden && !visibilityWasHidden) record('visibility_hidden');
      visibilityWasHidden = hidden;
    });
    window.addEventListener('blur', () => record('window_blur'));
  }

  const rating = document.getElementById('rating');
  const ratingValue = document.getElementById('rating-value');
  if (rating && ratingValue) {
    rating.addEventListener('input', () => { ratingValue.value = rating.value; });
  }

  const tutorialReplay = document.getElementById('tutorial-replay');
  if (tutorialReplay) {
    const tutorialSteps = [
      { letter: 'B', response: 'No match' },
      { letter: 'X', response: 'Match' },
      { letter: 'D', response: 'No match' },
    ];
    const tutorialLetter = document.getElementById('tutorial-letter');
    const tutorialResponse = document.getElementById('tutorial-response');
    let tutorialTimer = null;
    tutorialReplay.addEventListener('click', () => {
      window.clearTimeout(tutorialTimer);
      tutorialReplay.disabled = true;
      tutorialReplay.textContent = 'Playing…';
      let step = 0;
      const showStep = () => {
        const example = tutorialSteps[step];
        tutorialLetter.textContent = example.letter;
        tutorialResponse.textContent = example.response;
        step += 1;
        if (step < tutorialSteps.length) {
          tutorialTimer = window.setTimeout(showStep, 1100);
        } else {
          tutorialTimer = window.setTimeout(() => {
            tutorialReplay.disabled = false;
            tutorialReplay.textContent = 'Play again';
          }, 1100);
        }
      };
      showStep();
    });
  }

  const choiceForm = document.getElementById('choice-form');
  if (choiceForm) {
    const startedAt = performance.now();
    choiceForm.addEventListener('submit', (event) => {
      const submitter = event.submitter;
      if (!submitter || !['task', 'no_task'].includes(submitter.value)) {
        event.preventDefault();
        return;
      }
      document.getElementById('chosen-option').value = submitter.value;
      document.getElementById('response-time').value = Math.max(0, performance.now() - startedAt).toFixed(1);
      choiceForm.querySelectorAll('button[type="submit"]').forEach((button) => { button.disabled = true; });
    });
  }

  const taskConfigElement = document.getElementById('task-config');
  if (!taskConfigElement) return;

  const config = JSON.parse(taskConfigElement.textContent);
  const startButton = document.getElementById('start-task');
  const responseButtons = document.getElementById('task-responses');
  const stimulusElement = document.getElementById('stimulus');
  const statusElement = document.getElementById('task-status');
  const progressElement = document.getElementById('task-progress');
  const messageElement = document.getElementById('task-message');
  const errorElement = document.getElementById('task-error');
  const responses = [];
  let taskStartedAt = 0;
  let activeTrial = null;
  let running = false;

  if (matchMedia('(pointer: coarse)').matches) responseButtons.hidden = false;

  const recordResponse = (response) => {
    if (!running || !activeTrial || activeTrial.response !== null) return;
    activeTrial.response = response;
    activeTrial.reaction_time_ms = Math.max(0, performance.now() - activeTrial.stimulusStartedAt);
  };

  const finish = async () => {
    running = false;
    activeTrial = null;
    stimulusElement.textContent = '';
    statusElement.textContent = 'Saving responses…';
    messageElement.textContent = 'Please keep this page open while your responses are saved.';
    try {
      const result = await fetch(config.next_url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ responses }),
      });
      const body = await result.json();
      if (!result.ok) throw new Error(body.error || 'The task could not be saved.');
      window.location.assign(body.next_url);
    } catch (error) {
      statusElement.textContent = 'Responses not yet saved';
      messageElement.textContent = 'Keep this page open and retry the save.';
      errorElement.textContent = error.message;
      errorElement.hidden = false;
      const retryButton = document.createElement('button');
      retryButton.className = 'button button--primary';
      retryButton.type = 'button';
      retryButton.textContent = 'Retry save';
      retryButton.addEventListener('click', finish, { once: true });
      errorElement.after(retryButton);
    }
  };

  const showTrial = (index) => {
    if (index >= config.sequence.length) {
      finish();
      return;
    }
    const interval = config.stimulus_duration_ms + config.isi_ms;
    const scheduledTime = taskStartedAt + index * interval;
    window.setTimeout(() => {
      const stimulusStartedAt = performance.now();
      activeTrial = { response: null, reaction_time_ms: null, stimulusStartedAt };
      stimulusElement.textContent = config.sequence[index];
      progressElement.textContent = `${index + 1} / ${config.sequence.length}`;
      statusElement.textContent = `${Math.max(0, Math.ceil((config.duration_seconds * 1000 - (performance.now() - taskStartedAt)) / 1000))} seconds remaining`;
      window.setTimeout(() => { stimulusElement.textContent = ''; }, config.stimulus_duration_ms);
      window.setTimeout(() => {
        responses.push({ response: activeTrial?.response ?? null, reaction_time_ms: activeTrial?.reaction_time_ms ?? null });
        activeTrial = null;
        showTrial(index + 1);
      }, interval);
    }, Math.max(0, scheduledTime - performance.now()));
  };

  responseButtons.addEventListener('click', (event) => {
    const button = event.target.closest('[data-response]');
    if (button) recordResponse(button.dataset.response);
  });

  document.addEventListener('keydown', (event) => {
    if (!running || !activeTrial || activeTrial.response !== null || event.repeat) return;
    let response = null;
    if (event.code === 'Space') response = 'target';
    if (event.code === 'KeyF') response = 'non_target';
    if (!response) return;
    event.preventDefault();
    recordResponse(response);
  });

  startButton.addEventListener('click', () => {
    if (running) return;
    running = true;
    startButton.disabled = true;
    startButton.hidden = true;
    errorElement.hidden = true;
    messageElement.textContent = 'Tap Match or No match, or use Space for match and F for no match. Respond once to every letter.';
    taskStartedAt = performance.now();
    showTrial(0);
  });
})();