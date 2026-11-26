const STEPS = [...document.querySelectorAll('.step')];
const STORE_KEY = 'course-progress:' + CHAPTER;
const TASK_KEY = 'course-tasks:' + CHAPTER;
const COMPLETE_KEY = 'course-completed-steps:' + CHAPTER;
const CARD_KEY = 'course-cards:' + CHAPTER;
const VIEW_KEY = 'course-screen-v2:' + CHAPTER;
const THEME_KEY = 'course-theme-v2:' + CHAPTER;
const MODE_KEY = 'course-step-modes:' + CHAPTER;
const NOTES_KEY = 'course-notes:' + CHAPTER;
const READ_KEY = 'course-read:' + CHAPTER;
const QUIZ_KEY = 'course-questions:' + CHAPTER;
const sidebar = document.getElementById('course-sidebar');
const sidebarToggle = document.getElementById('sidebar-toggle');
const sidebarBackdrop = document.getElementById('sidebar-backdrop');
const drawerMedia = matchMedia('(max-width: 1000px)');
function loadRecord(key) {
  try { const value = JSON.parse(localStorage.getItem(key) || '{}'); return value && typeof value === 'object' && !Array.isArray(value) ? value : {}; } catch (_) { return {}; }
}
const stepModes = loadRecord(MODE_KEY);
const notes = loadRecord(NOTES_KEY);
const saved = loadRecord(STORE_KEY);
const solved = loadRecord(TASK_KEY);
const reviewed = loadRecord(CARD_KEY);
const completedSteps = loadRecord(COMPLETE_KEY);
const readSteps = loadRecord(READ_KEY);
const questionPositions = loadRecord(QUIZ_KEY);
// Keep lessons completed in earlier versions credited as studied.
STEPS.forEach(step => { if (completedSteps[step.dataset.stepId]) readSteps[step.dataset.stepId] = true; });
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
  STEPS.forEach((step, index) => {
    const stepId = step.dataset.stepId;
    const stepTasks = [...step.querySelectorAll('[data-task-id]')];
    const solvedCount = stepTasks.filter(task => solved[task.dataset.taskId]).length;
    const read = Boolean(readSteps[stepId]);
    const done = read && solvedCount === stepTasks.length;
    step.dataset.complete = String(done);
    if (done) completedCount += 1;
    if (Boolean(completedSteps[stepId]) !== done) {
      completedSteps[stepId] = done;
      changed = true;
      if (done) justCompleted.push(stepId);
    }
    const navigationItems = [...document.querySelectorAll('.step-link')].filter(item => item.dataset.stepId === stepId);
    navigationItems.forEach(nav => {
      const status = done ? 'Пройден' : read || solvedCount ? 'В процессе' : 'Не начат';
      nav.classList.toggle('is-complete', done);
      nav.querySelector('.step-marker').textContent = done ? '✓' : '○';
      nav.querySelector('.lesson-status').textContent = status;
      nav.setAttribute('aria-label', 'Урок ' + (index + 1) + '. ' + nav.querySelector('.step-name').textContent + ', ' + status.toLowerCase());
    });
    document.querySelectorAll('.core-track li').forEach(segment => {
      if (segment.dataset.stepId !== stepId) return;
      segment.classList.toggle('is-done', done);
      segment.classList.toggle('is-started', !done && (read || solvedCount > 0));
    });
    const reward = step.querySelector('[data-step-reward]');
    if (reward) {
      reward.textContent = solvedCount === stepTasks.length ? 'Все задания урока выполнены' : 'Выполнено заданий: ' + solvedCount + ' из ' + stepTasks.length;
      reward.classList.toggle('is-earned', solvedCount === stepTasks.length);
      const count = step.querySelector('[data-mode-count]');
      if (count) count.textContent = solvedCount + ' / ' + stepTasks.length;
    }
    step.querySelector('[data-step-mode="material"]').classList.toggle('is-done', read);
    step.querySelector('[data-step-mode="result"]').classList.toggle('is-done', done);
    step.querySelector('[data-reading-state]').textContent = read ? 'Материал отмечен как изученный.' : 'Отметьте материал изученным, когда закончите чтение.';
    const nextPhase = stepTasks.length ? 'К практике →' : 'Посмотреть итог →';
    step.querySelector('[data-read-step]').textContent = read ? nextPhase : 'Материал изучен · ' + nextPhase;
    step.querySelector('[data-result-title]').textContent = done ? 'Урок завершён' : 'Осталось завершить урок';
    step.querySelector('[data-result-description]').textContent = done
      ? 'Вы изучили материал' + (stepTasks.length ? ' и выполнили все практические задания.' : '.')
      : !read ? 'Вернитесь к материалу и отметьте его изученным. Затем завершите практику, если она есть в уроке.' : 'Выполните оставшиеся задания. Можно вернуться к объяснениям и использовать подсказки.';
    step.querySelector('[data-theory-state]').textContent = read ? '✓ Материал изучен' : '○ Материал ещё не отмечен как изученный';
    const practiceState = step.querySelector('[data-practice-state]');
    if (practiceState) practiceState.textContent = (solvedCount === stepTasks.length ? '✓ ' : '○ ') + 'Практика: ' + solvedCount + ' из ' + stepTasks.length;
    step.querySelector('[data-result-continue]').textContent = done
      ? index === STEPS.length - 1 ? 'К программе курса →' : 'Следующий урок →'
      : !read ? 'Вернуться к материалу' : 'Продолжить практику';
    updateQuestionControls(step);
  });
  document.getElementById('completed-steps').textContent = completedCount + ' / ' + STEPS.length;
  document.getElementById('sidebar-completed').textContent = completedCount + ' / ' + STEPS.length;
  document.getElementById('header-progress').textContent = 'Пройдено уроков: ' + completedCount + ' / ' + STEPS.length;
  document.getElementById('sidebar-progress-fill').style.width = (STEPS.length ? Math.round(completedCount / STEPS.length * 100) : 0) + '%';
  document.querySelector('.sidebar-track').setAttribute('aria-valuenow', String(completedCount));
  document.getElementById('completion-fill').style.width = (STEPS.length ? Math.round(completedCount / STEPS.length * 100) : 0) + '%';
  document.querySelector('.hero-track').setAttribute('aria-valuenow', String(completedCount));
  document.documentElement.style.setProperty('--progress', String(STEPS.length ? completedCount / STEPS.length : 0));
  const resumeButton = document.querySelector('[data-resume]');
  const nextIndex = STEPS.findIndex(step => step.dataset.complete !== 'true');
  const nextStep = STEPS[nextIndex < 0 ? 0 : nextIndex];
  const started = completedCount || Object.values(readSteps).some(Boolean) || Object.values(solved).some(Boolean);
  resumeButton.textContent = nextIndex < 0 ? 'Повторить курс →' : started ? 'Продолжить обучение →' : 'Начать обучение →';
  resumeButton.disabled = !STEPS.length;
  document.getElementById('resume-label').textContent = nextIndex < 0 ? 'КУРС ПРОЙДЕН' : started ? 'ПРОДОЛЖИТЬ С УРОКА ' + (nextIndex + 1) : 'ПЕРВЫЙ УРОК';
  document.getElementById('resume-title').textContent = nextStep?.querySelector('h1').textContent || 'В курсе пока нет уроков';
  document.getElementById('resume-description').textContent = nextIndex < 0 ? 'Вы можете повторить уроки или перейти к карточкам и конспекту.' : nextStep?.querySelector('.step-time')?.textContent || '';
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
function setDrawer(open, returnFocus = true) {
  const mobile = drawerMedia.matches;
  const wasOpen = document.body.classList.contains('nav-open');
  const wasInside = sidebar.contains(document.activeElement);
  const visible = mobile && Boolean(open);
  document.body.classList.toggle('nav-open', visible);
  sidebarToggle.setAttribute('aria-expanded', String(visible));
  sidebarToggle.setAttribute('aria-label', visible ? 'Закрыть меню курса' : 'Открыть меню курса');
  sidebar.inert = mobile && !visible;
  sidebar.setAttribute('aria-hidden', String(mobile && !visible));
  sidebarBackdrop.hidden = !visible;
  document.getElementById('course-content').inert = visible;
  document.querySelector('.masthead').inert = visible;
  if (visible) {
    sidebar.setAttribute('role', 'dialog');
    sidebar.setAttribute('aria-modal', 'true');
    document.getElementById('sidebar-close').focus({preventScroll: true});
  } else {
    sidebar.removeAttribute('role');
    sidebar.removeAttribute('aria-modal');
    if (mobile && returnFocus && (wasOpen || wasInside)) sidebarToggle.focus();
  }
}
sidebarToggle.addEventListener('click', () => setDrawer(!document.body.classList.contains('nav-open')));
document.getElementById('sidebar-close').addEventListener('click', () => setDrawer(false));
sidebarBackdrop.addEventListener('click', () => setDrawer(false));
drawerMedia.addEventListener('change', () => setDrawer(false));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && document.body.classList.contains('nav-open')) {
    event.preventDefault(); setDrawer(false);
  }
});
sidebar.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || !document.body.classList.contains('nav-open')) return;
  const controls = [...sidebar.querySelectorAll('button:not(:disabled),a[href],input,select,textarea')].filter(item => item.getClientRects().length > 0);
  const first = controls[0], last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
setDrawer(false, false);
let activeView = 'overview';
try { activeView = localStorage.getItem(VIEW_KEY) || 'overview'; } catch (_) {}
if (!['overview', 'course', 'cards', 'map', 'summary'].includes(activeView)) activeView = 'overview';
function setView(name) {
  setDrawer(false, false);
  activeView = name;
  document.querySelectorAll('.app-view').forEach(view => view.hidden = view.id !== name + '-view');
  document.querySelector('.layout').dataset.view = name;
  document.body.dataset.view = name;
  document.querySelectorAll('.sidebar-links [data-show-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.showView === name)));
  const heading = document.getElementById(name + '-view').querySelector(name === 'course' ? '.step:not([hidden]) h1' : 'h1');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  try { localStorage.setItem(VIEW_KEY, name); } catch (_) {}
}
function scrollToLesson() {
  const step = STEPS[current];
  if (!step) return;
  const heading = step.querySelector('h1');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  document.getElementById('course-view').scrollIntoView({block: 'start', behavior: 'instant'});
}
function setStepMode(step, mode, save = true) {
  const practice = step.querySelector('.practice-column');
  const selected = mode === 'result' ? 'result' : mode === 'practice' && practice ? 'practice' : 'material';
  step.dataset.mode = selected;
  step.querySelector('.lesson-column').hidden = selected !== 'material';
  if (practice) practice.hidden = selected !== 'practice';
  step.querySelector('.lesson-results').hidden = selected !== 'result';
  step.querySelectorAll('[data-step-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.stepMode === selected)));
  stepModes[step.dataset.stepId] = selected;
  if (save) persist(MODE_KEY, stepModes);
}
function lessonTasks(step) { return [...step.querySelectorAll('[data-task-id]')]; }
function selectQuestion(step, index, save = true) {
  const cards = lessonTasks(step);
  if (!cards.length) return;
  const position = Math.max(0, Math.min(Number(index) || 0, cards.length - 1));
  questionPositions[step.dataset.stepId] = position;
  cards.forEach((card, i) => { card.hidden = i !== position; });
  updateQuestionControls(step);
  if (save) {
    persist(QUIZ_KEY, questionPositions);
    const counter = step.querySelector('[data-question-counter]');
    counter.tabIndex = -1;
    counter.focus({preventScroll: true});
    counter.scrollIntoView({block: 'start', behavior: 'instant'});
  }
}
function updateQuestionControls(step) {
  const cards = lessonTasks(step);
  if (!cards.length) return;
  const position = Number(questionPositions[step.dataset.stepId]) || 0;
  step.querySelector('[data-question-counter]').textContent = 'Задание ' + (position + 1) + ' из ' + cards.length;
  step.querySelectorAll('[data-question]').forEach((button, i) => {
    const done = Boolean(solved[cards[i].dataset.taskId]);
    button.setAttribute('aria-pressed', String(position === i));
    button.setAttribute('aria-label', 'Задание ' + (i + 1) + (done ? ', выполнено' : ''));
    button.classList.toggle('is-solved', done);
  });
  step.querySelector('[data-question-previous]').disabled = position === 0;
  step.querySelector('[data-question-next]').textContent = position === cards.length - 1 ? 'Посмотреть итог →' : 'Следующее задание →';
}
function openLesson(index, mode) {
  setView('course');
  setStep(index);
  if (mode && STEPS[current]) setStepMode(STEPS[current], mode);
  scrollToLesson();
}
STEPS.forEach(step => {
  setStepMode(step, stepModes[step.dataset.stepId], false);
  step.querySelectorAll('[data-step-mode]').forEach(button => button.addEventListener('click', () => setStepMode(step, button.dataset.stepMode)));
  step.querySelector('[data-read-step]').addEventListener('click', () => {
    readSteps[step.dataset.stepId] = true;
    persist(READ_KEY, readSteps);
    syncCourseProgress();
    const cards = lessonTasks(step);
    const missing = cards.findIndex(card => !solved[card.dataset.taskId]);
    setStepMode(step, missing < 0 ? 'result' : 'practice');
    if (missing >= 0) selectQuestion(step, missing);
    scrollToLesson();
  });
  step.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => selectQuestion(step, Number(button.dataset.question))));
  step.querySelector('[data-question-previous]')?.addEventListener('click', () => selectQuestion(step, questionPositions[step.dataset.stepId] - 1));
  step.querySelector('[data-question-next]')?.addEventListener('click', () => {
    const position = Number(questionPositions[step.dataset.stepId]) || 0;
    if (position < lessonTasks(step).length - 1) selectQuestion(step, position + 1);
    else { setStepMode(step, 'result'); scrollToLesson(); }
  });
  step.querySelector('[data-result-continue]').addEventListener('click', () => {
    if (step.dataset.complete === 'true') {
      if (current < STEPS.length - 1) openLesson(current + 1, 'material');
      else { setView('overview'); window.scrollTo({top: 0, behavior: 'instant'}); }
    } else {
      setStepMode(step, readSteps[step.dataset.stepId] ? 'practice' : 'material');
      const missing = lessonTasks(step).findIndex(card => !solved[card.dataset.taskId]);
      if (missing >= 0) selectQuestion(step, missing);
      scrollToLesson();
    }
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
  openLesson(nextIndex < 0 ? 0 : nextIndex, nextIndex < 0 ? 'material' : undefined);
}
document.querySelectorAll('[data-show-view]').forEach(button => button.addEventListener('click', () => {
  if (button.hasAttribute('data-resume')) resumeLearning();
  else { setView(button.dataset.showView); window.scrollTo({top: 0, behavior: 'instant'}); }
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
  document.querySelectorAll('.step-link').forEach(button => {
    if (Number(button.dataset.go) === current) button.setAttribute('aria-current', 'step'); else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('.core-track li').forEach(segment => segment.classList.toggle('is-current', segment.dataset.stepId === STEPS[current].dataset.stepId));
  const percent = Math.round(((current + 1) / STEPS.length) * 100);
  document.getElementById('progress-fill').style.width = percent + '%';
  document.querySelector('.progress').setAttribute('aria-valuenow', String(current + 1));
  document.querySelector('.progress').setAttribute('aria-valuemax', String(STEPS.length));
  document.getElementById('progress-label').textContent = `Урок ${current + 1} из ${STEPS.length}`;
  document.getElementById('reader-title').textContent = STEPS[current].querySelector('h1').textContent;
  document.getElementById('previous').disabled = current === 0;
  document.getElementById('next').textContent = current === STEPS.length - 1 ? 'К программе курса →' : 'Следующий урок →';
  persist(STORE_KEY, {step: current});
}
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => openLesson(Number(button.dataset.go))));
document.getElementById('previous').addEventListener('click', () => openLesson(current - 1));
document.getElementById('next').addEventListener('click', () => {
  if (current < STEPS.length - 1) openLesson(current + 1, 'material');
  else { setView('overview'); window.scrollTo({top: 0, behavior: 'instant'}); }
});
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
document.getElementById('print-summary').addEventListener('click', () => { setView('summary'); window.print(); });
document.getElementById('reset-progress').addEventListener('click', () => {
  [STORE_KEY, TASK_KEY, CARD_KEY, COMPLETE_KEY, MODE_KEY, READ_KEY, QUIZ_KEY, VIEW_KEY].forEach(removeStored);
  location.reload();
});
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
  const instructions = document.createElement('div'); instructions.className = 'task-instructions';
  const instructionText = document.createElement('span');
  instructionText.textContent = ({single: 'Выберите один ответ', multiple: 'Выберите все подходящие варианты', order: 'Расставьте элементы по порядку', gap: 'Заполните пропуск', calc: 'Введите числовой ответ'})[task.type] || 'Практическое задание';
  instructions.append(instructionText);
  const hintBox = card.querySelector('.hint-box');
  const hintToggle = hintBox?.querySelector('.hint-toggle');
  const hintContent = hintBox?.querySelector('.hint-content');
  if (hintToggle) instructions.append(hintToggle);
  card.append(instructions);
  if (hintContent) card.append(hintContent);
  hintBox?.remove();
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
    (task.options || []).forEach((option, index) => { const label = document.createElement('label'); label.className = 'option'; const input = document.createElement('input'); input.type = task.type === 'multiple' ? 'checkbox' : 'radio'; input.name = 'answer-' + task.id; input.value = option.id; const key = document.createElement('span'); key.className = 'option-key'; key.setAttribute('aria-hidden', 'true'); key.textContent = index < 26 ? String.fromCharCode(65 + index) : String(index + 1); const text = document.createElement('span'); text.textContent = option.text; label.append(input, key, text); options.append(label); });
    card.append(options);
  }
  const controls = document.createElement('div'); controls.className = 'task-tools'; const check = document.createElement('button'); check.type = 'button'; check.textContent = 'Проверить'; controls.append(check); card.append(controls);
  const result = document.createElement('div'); result.className = 'task-result'; result.hidden = true; result.setAttribute('aria-live', 'polite'); card.append(result);
  card.querySelectorAll('.hint-toggle').forEach(button => button.addEventListener('click', () => {
    const hint = card.querySelector('.hint-content');
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
  function chosenMisconceptions() {
    if (task.type !== 'single' && task.type !== 'multiple') return [];
    const chosen = [...card.querySelectorAll('.option input:checked')].map(input => input.value);
    const ids = (task.options || []).filter(option => chosen.includes(String(option.id)) && option.misconception).map(option => String(option.misconception));
    return [...new Set(ids)].map(id => MISCONCEPTIONS[id]).filter(Boolean);
  }
  function showResult(correct) {
    result.hidden = false;
    result.replaceChildren();
    const verdict = document.createElement('strong'); verdict.className = 'result-verdict';
    verdict.textContent = correct ? 'Верно.' : 'Пока неверно.';
    result.append(verdict);
    if (!correct) {
      const right = document.createElement('p'); right.className = 'result-answer';
      right.textContent = 'Верный ответ: ' + expectedAnswer() + '.';
      result.append(right);
      chosenMisconceptions().forEach(text => {
        const myth = document.createElement('p'); myth.className = 'result-myth';
        const label = document.createElement('b'); label.textContent = 'Частое заблуждение: ';
        myth.append(label, document.createTextNode('«' + text + '» Это неверно — разберите объяснение ниже.'));
        result.append(myth);
      });
    }
    if (task.explanation) {
      const why = document.createElement('p'); why.className = 'result-why';
      why.textContent = task.explanation;
      if (task.explanation_by_model === true) { const mark = document.createElement('span'); mark.className = 'badge'; mark.textContent = 'написано моделью'; why.append(' ', mark); }
      result.append(why);
    }
    result.classList.toggle('correct', correct);
    result.classList.toggle('wrong', !correct);
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
    if (firstSolve && newMilestones.includes(card.closest('.step').dataset.stepId)) { const done = document.createElement('p'); done.className = 'result-milestone'; done.textContent = 'Урок завершён. Можно посмотреть итог.'; result.append(done); }
    if (correct) restoreAndLockAnswer();
  });
}
TASKS.forEach(taskElement);
const scanDialog = document.getElementById('scan-dialog');
document.addEventListener('click', event => {
  const link = event.target.closest('.scan-link');
  if (link && scanDialog) {
    const image = scanDialog.querySelector('img');
    image.src = link.dataset.scan;
    image.alt = 'Скан страницы книги, ' + link.textContent;
    scanDialog.querySelector('#scan-title').textContent = 'Скан: ' + link.textContent;
    if (typeof scanDialog.showModal === 'function') scanDialog.showModal(); else scanDialog.setAttribute('open', '');
    return;
  }
  if (event.target.closest('[data-close-scan]') || event.target === scanDialog) scanDialog.close?.();
});
STEPS.forEach(step => {
  const cards = lessonTasks(step);
  const firstUnsolved = cards.findIndex(card => !solved[card.dataset.taskId]);
  selectQuestion(step, questionPositions[step.dataset.stepId] ?? Math.max(0, firstUnsolved), false);
});
syncCourseProgress();
setStep(current);
setView(activeView);
