/* Optional templates use only the current node's data. No network dependencies. */
function pressureE1(x) {
  if (!(x > 0)) return Infinity;
  if (x <= 1) {
    let value = -0.5772 - Math.log(x);
    let term = 1;
    for (let n = 1; n <= 30; n++) {
      term *= x / n;
      value += (n % 2 ? 1 : -1) * term / n;
    }
    return value;
  }
  return (x * x + 2.334733 * x + 0.250621)
    / (x * x + 3.330657 * x + 1.681534) / (x * Math.exp(x));
}

function pressureAt(distance, kappa, seconds) {
  const radius = Math.max(0.1, distance);
  return 20 - 0.08 * pressureE1(radius * radius / (4 * kappa * seconds));
}

function pressureSeconds(position) {
  return 10 * Math.exp(Number(position) / 1000 * Math.log(30 * 86400 / 10));
}

function pressureTimeLabel(seconds) {
  const units = seconds >= 86400 ? [86400, 'сут'] : seconds >= 3600 ? [3600, 'ч'] : seconds >= 60 ? [60, 'мин'] : [1, 'с'];
  return (seconds / units[0]).toLocaleString('ru-RU', {maximumFractionDigits: 2}) + ' ' + units[1];
}

function initDataPracticum(block) {
  const observations = block.querySelector('[data-observation-form]');
  const conclusions = block.querySelector('[data-conclusion-form]');
  const result = block.querySelector('.decision-result');
  observations.addEventListener('change', () => {
    observations.querySelector('[type="submit"]').disabled = !observations.querySelector('input:checked');
  });
  observations.addEventListener('submit', event => {
    event.preventDefault();
    if (!observations.querySelector('input:checked')) return;
    observations.hidden = true;
    conclusions.hidden = false;
    conclusions.querySelector('legend').focus();
  });
  conclusions.addEventListener('change', () => {
    conclusions.querySelector('[type="submit"]').disabled = !conclusions.querySelector('input:checked');
    result.hidden = true;
  });
  conclusions.addEventListener('submit', event => {
    event.preventDefault();
    const answer = conclusions.querySelector('input:checked');
    if (!answer) return;
    const correct = answer.dataset.correct === 'true';
    result.hidden = false;
    result.classList.toggle('is-correct', correct);
    result.querySelector('[data-decision-feedback]').textContent = correct
      ? 'Верно. Сопоставьте свой вывод с правилом и его ограничениями.'
      : 'Пока неверно. Сравните выбранный вариант с выводом из слоя и правилом автора. Можно выбрать другой ответ.';
    result.scrollIntoView({block: 'nearest', behavior: 'instant'});
  });
  block.querySelector('[data-back-to-features]').addEventListener('click', () => {
    conclusions.hidden = true;
    observations.hidden = false;
    result.hidden = true;
    observations.querySelector('input').focus();
  });
}

function initPressureExplorer(block) {
  const kappa = block.querySelector('[data-kappa]');
  const time = block.querySelector('[data-pressure-time]');
  const chart = block.querySelector('.pressure-chart');
  const drawing = block.querySelector('[data-pressure-drawing]');
  const svgNS = 'http://www.w3.org/2000/svg';
  function element(tag, attributes, text) {
    const node = document.createElementNS(svgNS, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function draw() {
    const diffusivity = Number(kappa.value);
    const seconds = pressureSeconds(time.value);
    const kappaLabel = diffusivity.toLocaleString('ru-RU', {maximumFractionDigits: 1}) + ' м²/с';
    const timeLabel = pressureTimeLabel(seconds);
    block.querySelector('[data-kappa-output]').textContent = kappaLabel;
    block.querySelector('[data-time-output]').textContent = timeLabel;
    kappa.setAttribute('aria-valuetext', kappaLabel);
    time.setAttribute('aria-valuetext', timeLabel);
    const width = Math.max(280, Math.round(chart.getBoundingClientRect().width) || 640);
    const height = 340;
    chart.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const left = 56, right = width - 20, top = 36, bottom = height - 50;
    const px = r => left + r / 100 * (right - left);
    const py = p => bottom - (p - 18) / 2.05 * (bottom - top);
    const nodes = [];
    for (let p = 18; p <= 20; p += 0.5) {
      nodes.push(element('line', {x1: left, x2: right, y1: py(p), y2: py(p), class: 'chart-grid'}));
      nodes.push(element('text', {x: left - 8, y: py(p) + 5, 'text-anchor': 'end'}, p.toLocaleString('ru-RU')));
    }
    for (let r = 0; r <= 100; r += 25) {
      nodes.push(element('line', {x1: px(r), x2: px(r), y1: top, y2: bottom, class: 'chart-grid'}));
      nodes.push(element('text', {x: px(r), y: bottom + 24, 'text-anchor': 'middle'}, r));
    }
    nodes.push(element('text', {x: left, y: 20}, 'p, МПа'));
    nodes.push(element('text', {x: right, y: height - 5, 'text-anchor': 'end'}, 'Расстояние r, м'));
    const points = Array.from({length: 201}, (_, i) => {
      const r = i / 2;
      return `${i ? 'L' : 'M'}${px(r).toFixed(2)},${py(pressureAt(r, diffusivity, seconds)).toFixed(2)}`;
    }).join(' ');
    nodes.push(element('path', {d: points + ` L${right},${py(20)} L${left},${py(20)} Z`, class: 'pressure-area'}));
    nodes.push(element('path', {d: points, class: 'pressure-curve', 'data-near-pressure': pressureAt(0, diffusivity, seconds).toFixed(6), 'data-far-pressure': pressureAt(100, diffusivity, seconds).toFixed(6)}));
    drawing.replaceChildren(...nodes);
    chart.querySelector('desc').textContent = `κ = ${kappaLabel}, t = ${timeLabel}. Давление у скважины ${pressureAt(0, diffusivity, seconds).toFixed(2)} МПа, на расстоянии 100 м ${pressureAt(100, diffusivity, seconds).toFixed(2)} МПа. Симуляция по формуле главы, условные параметры.`;
  }
  kappa.addEventListener('input', draw);
  time.addEventListener('input', draw);
  draw();
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(draw).observe(chart);
  else window.addEventListener('resize', draw);
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('.data-practicum').forEach(initDataPracticum);
  document.querySelectorAll('.pressure-explorer').forEach(initPressureExplorer);
}
