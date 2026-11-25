const STEPS = [...document.querySelectorAll('.step')];
const STORE_KEY = 'course-progress:' + CHAPTER;
const TASK_KEY = 'course-tasks:' + CHAPTER;
const COMPLETE_KEY = 'course-completed-steps:' + CHAPTER;
const CARD_KEY = 'course-cards:' + CHAPTER;
const VIEW_KEY = 'course-view:' + CHAPTER;
const THEME_KEY = 'course-theme-v2:' + CHAPTER;
const MODE_KEY = 'course-step-modes:' + CHAPTER;
const NOTES_KEY = 'course-notes:' + CHAPTER;
let saved = {};
let solved = {};
let reviewed = {};
let completedSteps = {};
function loadRecord(key) {
  try { const value = JSON.parse(localStorage.getItem(key) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch (_) { return {}; }
}
const stepModes = loadRecord(MODE_KEY);
const notes = loadRecord(NOTES_KEY);
try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch (_) {}
try { solved = JSON.parse(localStorage.getItem(TASK_KEY) || '{}'); } catch (_) {}
try { reviewed = JSON.parse(localStorage.getItem(CARD_KEY) || '{}'); } catch (_) {}
try { completedSteps = JSON.parse(localStorage.getItem(COMPLETE_KEY) || '{}'); } catch (_) {}
let current = Math.min(Math.max(Number(saved.step) || 0, 0), Math.max(STEPS.length - 1, 0));
const esc = (s) => String(s ?? '');
function persist(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch (_) { return false; } }
function removeStored(key) { try { localStorage.removeItem(key); } catch (_) {} }
function updateLearningProgress() {
  const taskCards = [...document.querySelectorAll('[data-task-id]')];
  const solvedCount = taskCards.filter(card => solved[card.dataset.taskId]).length;
  document.getElementById('solved-tasks').textContent = solvedCount + ' / ' + taskCards.length;
}
function syncCourseProgress() {
  let completedCount = 0;
  let changed = false;
  const justCompleted = [];
  const validTypes = ['single', 'multiple', 'order', 'gap', 'calc'];
  STEPS.forEach(step => {
    const stepId = step.dataset.stepId;
    const stepTasks = TASKS.filter(task => task.step_id === stepId && validTypes.includes(task.type) && task.id && task.prompt && Object.hasOwn(task, 'answer'));
    const done = stepTasks.length ? stepTasks.every(task => solved[task.id]) : Boolean(completedSteps[stepId]);
    step.dataset.complete = String(done);
    if (done) {
      completedCount += 1;
      if (!completedSteps[stepId]) {
        completedSteps[stepId] = true;
        changed = true;
        justCompleted.push(stepId);
      }
    }
    const nav = [...document.querySelectorAll('.step-link')].find(item => item.dataset.stepId === stepId);
    if (nav) {
      nav.classList.toggle('is-complete', done);
      nav.querySelector('.step-marker').textContent = done ? '✓' : '○';
      nav.setAttribute('aria-label', nav.querySelector('.step-name').textContent + (done ? ', пройдено' : ', не пройдено'));
    }
    const reward = step.querySelector('[data-step-reward]');
    if (reward) {
      const solvedCount = stepTasks.filter(task => solved[task.id]).length;
      reward.textContent = done ? 'Все задания этапа выполнены' : 'Выполнено заданий: ' + solvedCount + ' из ' + stepTasks.length;
      reward.classList.toggle('is-earned', done);
      const count = step.querySelector('[data-mode-count]');
      if (count) count.textContent = solvedCount + ' / ' + stepTasks.length;
    }
    const completionButton = step.querySelector('[data-complete-step]');
    if (completionButton) {
      completionButton.disabled = done;
      completionButton.textContent = done ? 'Шаг изучен' : 'Отметить шаг как изученный';
    }
  });
  document.getElementById('completed-steps').textContent = completedCount + ' / ' + STEPS.length;
  document.getElementById('completion-fill').style.width = (STEPS.length ? Math.round(completedCount / STEPS.length * 100) : 0) + '%';
  document.querySelector('.hero-track').setAttribute('aria-valuenow', String(completedCount));
  const resumeButton = document.querySelector('[data-resume]');
  resumeButton.textContent = STEPS.length && completedCount === STEPS.length ? 'Повторить материал →' : 'Продолжить обучение →';
  resumeButton.disabled = !STEPS.length;
  if (changed) {
    persist(COMPLETE_KEY, completedSteps);
  }
  updateLearningProgress();
  return justCompleted;
}
let theme = '';
try { theme = localStorage.getItem(THEME_KEY) || ''; } catch (_) {}
if (!['light', 'dark'].includes(theme)) theme = 'light';
document.documentElement.dataset.theme = theme;
const themeToggle = document.getElementById('theme-toggle');
function updateThemeButton() { themeToggle.textContent = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'; themeToggle.setAttribute('aria-label', theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'); }
themeToggle.addEventListener('click', () => { theme = theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme; try { localStorage.setItem(THEME_KEY, theme); } catch (_) {} updateThemeButton(); });
updateThemeButton();
let activeView = 'course';
try { activeView = localStorage.getItem(VIEW_KEY) || 'course'; } catch (_) {}
if (!['course', 'cards', 'map', 'summary'].includes(activeView)) activeView = 'course';
function setView(name) {
  activeView = name;
  document.querySelectorAll('.app-view').forEach(view => view.hidden = view.id !== name + '-view');
  document.getElementById('course-nav').hidden = name !== 'course';
  document.querySelector('.layout').dataset.view = name;
  document.body.dataset.view = name;
  document.querySelectorAll('.view-switcher [data-show-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.showView === name)));
  try { localStorage.setItem(VIEW_KEY, name); } catch (_) {}
}
function scrollToLesson() {
  const step = STEPS[current];
  if (!step) return;
  const heading = step.querySelector('h1');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  step.scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
}
function setStepMode(step, mode, save = true) {
  const practice = step.querySelector('.practice-column');
  const selected = mode === 'practice' && practice ? 'practice' : 'material';
  step.dataset.mode = selected;
  step.querySelector('.lesson-column').hidden = selected !== 'material';
  if (practice) practice.hidden = selected !== 'practice';
  step.querySelectorAll('[data-step-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.stepMode === selected)));
  stepModes[step.dataset.stepId] = selected;
  if (save) persist(MODE_KEY, stepModes);
}
STEPS.forEach(step => {
  setStepMode(step, stepModes[step.dataset.stepId], false);
  step.querySelectorAll('[data-step-mode]').forEach(button => button.addEventListener('click', () => setStepMode(step, button.dataset.stepMode)));
  step.querySelector('[data-open-practice]')?.addEventListener('click', () => {
    setStepMode(step, 'practice');
    step.querySelector('[data-step-mode="practice"]').focus({preventScroll: true});
    step.scrollIntoView({block: 'start'});
  });
  const input = step.querySelector('[data-step-note]');
  const status = step.querySelector('.notes-status');
  input.value = typeof notes[step.dataset.stepId] === 'string' ? notes[step.dataset.stepId] : '';
  if (input.value) status.textContent = 'Сохранено на этом устройстве.';
  input.addEventListener('input', () => {
    notes[step.dataset.stepId] = input.value;
    const message = persist(NOTES_KEY, notes) ? 'Сохранено на этом устройстве.' : 'Сохранение недоступно. Скопируйте заметку перед закрытием страницы.';
    if (status.textContent !== message) status.textContent = message;
  });
});
function resumeLearning() {
  const nextIndex = STEPS.findIndex(step => step.dataset.complete !== 'true');
  setView('course');
  setStep(nextIndex < 0 ? 0 : nextIndex);
  if (nextIndex < 0 && STEPS[current]) setStepMode(STEPS[current], 'material');
  scrollToLesson();
}
document.querySelectorAll('[data-show-view]').forEach(button => button.addEventListener('click', () => {
  if (button.hasAttribute('data-resume')) resumeLearning();
  else setView(button.dataset.showView);
}));
document.addEventListener('click', event => {
  const link = event.target.closest('.task a[href^="#claim-"]');
  if (!link) return;
  const target = document.getElementById(link.getAttribute('href').slice(1));
  const step = target?.closest('.step');
  if (!step) return;
  event.preventDefault();
  setView('course');
  setStep(STEPS.indexOf(step));
  setStepMode(step, 'material');
  target.tabIndex = -1;
  target.focus({preventScroll: true});
  target.scrollIntoView({block: 'start'});
});
function setStep(index) {
  if (!STEPS.length) return;
  current = Math.min(Math.max(index, 0), STEPS.length - 1);
  STEPS.forEach((step, i) => step.hidden = i !== current);
  document.querySelectorAll('.step-link').forEach((button, i) => {
    if (i === current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  });
  const percent = Math.round(((current + 1) / STEPS.length) * 100);
  document.getElementById('progress-fill').style.width = percent + '%';
  document.querySelector('.progress').setAttribute('aria-valuenow', String(current + 1));
  document.querySelector('.progress').setAttribute('aria-valuemax', String(STEPS.length));
  document.getElementById('progress-label').textContent = `${current + 1} из ${STEPS.length} шагов`;
  document.getElementById('previous').disabled = current === 0;
  document.getElementById('next').disabled = current === STEPS.length - 1;
  persist(STORE_KEY, {step: current});
}
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => { setView('course'); setStep(Number(button.dataset.go)); scrollToLesson(); }));
document.getElementById('previous').addEventListener('click', () => { setStep(current - 1); scrollToLesson(); });
document.getElementById('next').addEventListener('click', () => { setStep(current + 1); scrollToLesson(); });
document.querySelectorAll('.review-card').forEach(card => {
  const id = card.dataset.reviewId;
  card.querySelectorAll('[data-review]').forEach(button => {
    button.setAttribute('aria-pressed', String(reviewed[id] === button.dataset.review));
    button.addEventListener('click', () => {
      reviewed[id] = button.dataset.review;
      persist(CARD_KEY, reviewed);
      card.querySelectorAll('[data-review]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    });
  });
});
document.querySelectorAll('[data-complete-step]').forEach(button => button.addEventListener('click', () => {
  const stepId = button.dataset.completeStep;
  if (!completedSteps[stepId]) {
    completedSteps[stepId] = true;
    persist(COMPLETE_KEY, completedSteps);
    button.textContent = 'Шаг изучен';
    button.disabled = true;
    syncCourseProgress();
  }
}));
document.getElementById('print-summary').addEventListener('click', () => { setView('summary'); window.print(); });
document.getElementById('reset-progress').addEventListener('click', () => { removeStored(STORE_KEY); removeStored(TASK_KEY); removeStored(CARD_KEY); removeStored(COMPLETE_KEY); removeStored(MODE_KEY); location.reload(); });
function taskElement(task) {
  const card = document.querySelector(`[data-task-id="${CSS.escape(task.id)}"]`);
  if (!card) return;
  const prompt = document.createElement('p'); prompt.className = 'task-prompt';
  if (task.type === 'gap') {
    const parts = String(task.prompt || '').split(/(\{\{\d+\}\})/g);
    parts.forEach(part => { if (/^\{\{\d+\}\}$/.test(part)) { const input = document.createElement('input'); input.className = 'gap-input'; input.type = 'text'; input.dataset.gap = part.match(/\d+/)[0]; input.setAttribute('aria-label', 'Ответ в пропуск'); prompt.append(input); } else prompt.append(document.createTextNode(part)); });
  } else prompt.textContent = esc(task.prompt);
  if (task.by_model === true) { const mark = document.createElement('span'); mark.className = 'badge'; mark.textContent = 'написано моделью'; prompt.append(' ', mark); }
  const taskSource = card.querySelector('[data-task-source]');
  if (taskSource) prompt.append(' ', taskSource);
  card.append(prompt);
  if (task.type === 'calc') {
    const input = document.createElement('input'); input.type = 'number'; input.step = 'any'; input.className = 'gap-input'; input.dataset.calc = 'true'; input.setAttribute('aria-label', 'Числовой ответ'); card.append(input);
    if (task.unit) { const unit = document.createElement('span'); unit.textContent = ' ' + task.unit; card.append(unit); }
  } else if (task.type === 'order') {
    const options = [...(task.options || [])];
    let seed = String(task.id || '').split('').reduce((n, c) => (n * 31 + c.charCodeAt(0)) >>> 0, 7);
    for (let i = options.length - 1; i > 0; i--) { seed = (seed * 1664525 + 1013904223) >>> 0; const j = seed % (i + 1); [options[i], options[j]] = [options[j], options[i]]; }
    const list = document.createElement('div'); list.className = 'options order-list'; list.setAttribute('aria-label', 'Переставьте варианты в нужном порядке');
    options.forEach(option => { const row = document.createElement('div'); row.className = 'order-item'; row.dataset.id = option.id; row.draggable = true;
      const handle = document.createElement('button'); handle.className = 'drag-handle'; handle.type = 'button'; handle.textContent = '↕'; handle.setAttribute('aria-label', 'Перетаскивать вариант');
      const text = document.createElement('span'); text.className = 'order-text'; text.textContent = option.text;
      const up = document.createElement('button'); up.type = 'button'; up.textContent = '↑'; up.setAttribute('aria-label', 'Переместить вверх');
      const down = document.createElement('button'); down.type = 'button'; down.textContent = '↓'; down.setAttribute('aria-label', 'Переместить вниз');
      up.addEventListener('click', () => { if (row.previousElementSibling) list.insertBefore(row, row.previousElementSibling); });
      down.addEventListener('click', () => { if (row.nextElementSibling) list.insertBefore(row.nextElementSibling, row); });
      row.append(handle, text, up, down); list.append(row);
      row.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', option.id); row.classList.add('dragging'); });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
    });
    list.addEventListener('dragover', e => { e.preventDefault(); const dragged = list.querySelector('.dragging'); const target = e.target.closest('.order-item'); if (dragged && target && dragged !== target) { const rect = target.getBoundingClientRect(); list.insertBefore(dragged, e.clientY < rect.top + rect.height / 2 ? target : target.nextSibling); } });
    let pointerRow = null;
    list.addEventListener('pointerdown', e => { const handle = e.target.closest('.drag-handle'); if (!handle || handle.disabled) return; pointerRow = handle.closest('.order-item'); pointerRow.classList.add('dragging'); handle.setPointerCapture(e.pointerId); e.preventDefault(); });
    list.addEventListener('pointermove', e => { if (!pointerRow) return; const target = document.elementFromPoint(e.clientX, e.clientY)?.closest('.order-item'); if (target && target !== pointerRow) { const rect = target.getBoundingClientRect(); list.insertBefore(pointerRow, e.clientY < rect.top + rect.height / 2 ? target : target.nextSibling); } });
    list.addEventListener('pointerup', () => { if (pointerRow) pointerRow.classList.remove('dragging'); pointerRow = null; });
    list.addEventListener('pointercancel', () => { if (pointerRow) pointerRow.classList.remove('dragging'); pointerRow = null; });
    card.append(list);
  } else if (task.type !== 'gap' && task.type !== 'calc') {
    const options = document.createElement('div'); options.className = 'options';
    (task.options || []).forEach(option => { const label = document.createElement('label'); label.className = 'option'; const input = document.createElement('input'); input.type = task.type === 'multiple' ? 'checkbox' : 'radio'; input.name = 'answer-' + task.id; input.value = option.id; const text = document.createElement('span'); text.textContent = option.text; label.append(input, text); options.append(label); });
    card.append(options);
  }
  const controls = document.createElement('div'); controls.className = 'task-tools'; const check = document.createElement('button'); check.type = 'button'; check.textContent = 'Проверить'; controls.append(check); card.append(controls);
  const result = document.createElement('div'); result.className = 'task-result'; result.hidden = true; result.setAttribute('aria-live', 'polite'); card.append(result);
  card.querySelectorAll('.hint-toggle').forEach(button => button.addEventListener('click', () => {
    const hint = button.nextElementSibling;
    hint.hidden = !hint.hidden;
    button.setAttribute('aria-expanded', String(!hint.hidden));
    button.textContent = hint.hidden ? 'Подсказка' : 'Скрыть подсказку';
  }));
  function restoreAndLockAnswer() {
    if (task.type === 'single' || task.type === 'multiple') {
      const expected = (Array.isArray(task.answer) ? task.answer : [task.answer]).map(String);
      card.querySelectorAll('.option input').forEach(input => { input.checked = expected.includes(input.value); });
    } else if (task.type === 'gap') {
      const expected = Array.isArray(task.answer) ? task.answer : [task.answer];
      [...card.querySelectorAll('[data-gap]')].sort((a, b) => Number(a.dataset.gap) - Number(b.dataset.gap)).forEach((input, index) => { input.value = String(expected[index] ?? ''); });
    } else if (task.type === 'calc') {
      card.querySelector('[data-calc]').value = String(task.answer);
    } else if (task.type === 'order') {
      const list = card.querySelector('.order-list');
      const rows = [...list.querySelectorAll('.order-item')];
      (Array.isArray(task.answer) ? task.answer : []).forEach(id => {
        const row = rows.find(item => item.dataset.id === String(id));
        if (row) list.append(row);
      });
      rows.forEach(row => { row.draggable = false; });
      list.querySelectorAll('button').forEach(button => { button.disabled = true; });
    }
    card.querySelectorAll('input').forEach(input => { input.disabled = true; });
    check.disabled = true;
  }
  if (solved[task.id]) { restoreAndLockAnswer(); showResult(true); }
  function expectedAnswer() {
    const optionText = id => (task.options || []).find(option => String(option.id) === String(id))?.text ?? String(id);
    if (task.type === 'single') return optionText(task.answer);
    if (task.type === 'multiple' || task.type === 'order') return (Array.isArray(task.answer) ? task.answer : []).map(optionText).join(task.type === 'order' ? ' → ' : ', ');
    if (task.type === 'gap') return (Array.isArray(task.answer) ? task.answer : [task.answer]).join(', ');
    if (task.type === 'calc') return String(task.answer) + (task.unit ? ' ' + task.unit : '');
    return String(task.answer ?? '');
  }
  function showResult(correct) {
    result.hidden = false;
    const explanation = task.explanation || '';
    const modelMark = task.explanation_by_model === true ? ' · написано моделью' : '';
    result.textContent = (correct ? 'Верно. ' : 'Пока неверно. Верный ответ: ' + expectedAnswer() + '. ') + explanation + modelMark;
    result.classList.toggle('correct', correct);
  }
  check.addEventListener('click', () => {
    let answer;
    if (task.type === 'order') answer = [...card.querySelectorAll('.order-item')].map(row => row.dataset.id);
    else if (task.type === 'gap') answer = [...card.querySelectorAll('[data-gap]')].sort((a,b) => Number(a.dataset.gap)-Number(b.dataset.gap)).map(input => input.value.trim());
    else if (task.type === 'calc') { const input = card.querySelector('[data-calc]'); answer = input.value.trim() === '' ? NaN : Number(input.value); }
    else answer = [...card.querySelectorAll('input:checked')].map(input => input.value);
    let correct = false;
    if (task.type === 'single') correct = answer.length === 1 && answer[0] === String(task.answer);
    else if (task.type === 'multiple') correct = Array.isArray(task.answer) && answer.length === task.answer.length && [...answer].sort().every((v, i) => String(v) === String([...task.answer].map(String).sort()[i]));
    else if (task.type === 'order') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => String(v) === String(task.answer[i]));
    else if (task.type === 'gap') correct = Array.isArray(task.answer) && answer.length === task.answer.length && answer.every((v, i) => v.trim().replace(/\s+/g, ' ').localeCompare(String(task.answer[i]).trim().replace(/\s+/g, ' '), undefined, {sensitivity:'base'}) === 0);
    else if (task.type === 'calc') correct = Number.isFinite(answer) && Math.abs(answer - Number(task.answer)) <= Number(task.tolerance || 0);
    const firstSolve = correct && !solved[task.id];
    if (firstSolve) {
      solved[task.id] = true;
      persist(TASK_KEY, solved);
    }
    const newMilestones = syncCourseProgress();
    showResult(correct);
    if (firstSolve && newMilestones.includes(card.closest('.step').dataset.stepId)) result.textContent += ' Все задания этапа выполнены.';
    if (correct) restoreAndLockAnswer();
  });
}
TASKS.forEach(taskElement);
syncCourseProgress();
setStep(current);
setView(activeView);
