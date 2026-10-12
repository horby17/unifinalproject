(() => {
  const source = document.getElementById('dashboard-statistics');
  if (!source) return;
  const data = JSON.parse(source.textContent);
  const keys = ['Bueno', 'Nulo', 'Sin evaluar'];
  const colors = ['#2563eb', '#14b8a6', '#f59e0b'];
  const movements = {Squat: 'Sentadilla', Bench: 'Press de banca', Deadlift: 'Peso muerto'};
  const node = (tag, attrs = {}, text) => {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const svg = (id, width, height, title) => {
    const container = document.getElementById(id);
    if (!container) return null;
    const chart = node('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': title});
    chart.append(node('title', {}, title));
    container.append(chart);
    return chart;
  };
  const label = (chart, x, y, text, attrs = {}) => chart.append(node('text', {x, y, fill: 'currentColor', 'font-size': 12, ...attrs}, text));
  const pie = svg('results-pie', 400, 290, keys.map(key => `${key}: ${data.counts[key]}`).join(', '));
  if (pie && data.total) {
    let angle = -Math.PI / 2;
    keys.forEach((key, i) => {
      const count = data.counts[key];
      if (!count) return;
      const fraction = count / data.total;
      const end = angle + fraction * Math.PI * 2;
      let segment;
      if (fraction === 1) segment = node('circle', {cx: 200, cy: 145, r: 112, fill: colors[i]});
      else segment = node('path', {d: `M 200 145 L ${200 + 112 * Math.cos(angle)} ${145 + 112 * Math.sin(angle)} A 112 112 0 ${fraction > .5 ? 1 : 0} 1 ${200 + 112 * Math.cos(end)} ${145 + 112 * Math.sin(end)} Z`, fill: colors[i], stroke: 'white', 'stroke-width': 2});
      segment.append(node('title', {}, `${key}: ${count} (${(fraction * 100).toFixed(1)}%)`));
      pie.append(segment);
      angle = end;
    });
  }
  const max = Math.max(1, ...data.groups.map(group => Math.max(...keys.map(key => group[key]))));
  const chartWidth = Math.max(540, data.groups.length * 125 + 80);
  const bars = svg('results-bars', chartWidth, 330, 'Cantidad de resultados por atleta y movimiento. Valores detallados en la tabla de resumen.');
  if (bars) {
    bars.style.width = `${chartWidth}px`;
    bars.style.minWidth = "100%";
    const ceiling = Math.max(1, Math.ceil(max / 4) * 4);
    [0, 1, 2, 3, 4].forEach(tick => {
      const y = 230 - tick * 50;
      bars.append(node('line', {x1: 45, x2: chartWidth - 20, y1: y, y2: y, stroke: 'currentColor', opacity: .12}));
      label(bars, 36, y + 4, ceiling * tick / 4, {'text-anchor': 'end'});
    });
    const slot = (chartWidth - 75) / Math.max(1, data.groups.length);
    data.groups.forEach((group, index) => {
      const center = 45 + slot * (index + .5);
      const orderedResults = keys.filter(key => group[key] > 0)
        .sort((a, b) => group[b] - group[a]);
      orderedResults.forEach((key, i) => {
        const height = group[key] / ceiling * 200;
        const x = center + (i - (orderedResults.length - 1) / 2) * 24 - 10;
        const rect = node('rect', {x, y: 230 - height, width: 20, height, rx: 3, fill: colors[keys.indexOf(key)]});
        rect.append(node('title', {}, `${group.athlete}, ${group.movement}, ${key}: ${group[key]}`));
        bars.append(rect);
        label(bars, x + 10, 222 - height, group[key], {'text-anchor': 'middle'});
      });
      const name = group.athlete.length > 17 ? group.athlete.slice(0, 16) + '…' : group.athlete;
      label(bars, center, 253, name, {'text-anchor': 'middle'});
      label(bars, center, 274, movements[group.movement] || group.movement, {'text-anchor': 'middle', 'font-size': 10});
      label(bars, center, 292, `#${group.athlete_id}`, {'text-anchor': 'middle', 'font-size': 10});
    });
  }
  const line = svg('results-line', 800, 280, 'Porcentaje acumulado de intentos buenos entre los evaluados, en orden de registro.');
  if (line) {
    if (!data.trend.length) { label(line, 400, 140, 'No hay intentos evaluados para mostrar una evolución.', {'text-anchor': 'middle'}); return; }
    [0, 25, 50, 75, 100].forEach(value => {
      const y = 230 - value * 1.9;
      line.append(node('line', {x1: 60, x2: 755, y1: y, y2: y, stroke: 'currentColor', opacity: .15}));
      label(line, 48, y + 4, `${value}%`, {'text-anchor': 'end'});
    });
    const points = data.trend.map((point, i) => ({...point, x: data.trend.length === 1 ? 407 : 60 + i * 695 / (data.trend.length - 1), y: 230 - point.rate * 1.9}));
    line.append(node('polyline', {points: points.map(p => `${p.x},${p.y}`).join(' '), fill: 'none', stroke: '#2563eb', 'stroke-width': 3}));
    points.forEach((p, i) => {
      const dot = node('circle', {cx: p.x, cy: p.y, r: 4, fill: '#2563eb'});
      dot.append(node('title', {}, `Intento #${p.attempt}: ${p.rate}%`));
      line.append(dot);
      if (i === 0 || i === points.length - 1) label(line, p.x, 251, `#${p.attempt}`, {'text-anchor': 'middle'});
    });
    label(line, 407, 276, 'Intentos evaluados en orden de registro', {'text-anchor': 'middle'});
  }
})();
