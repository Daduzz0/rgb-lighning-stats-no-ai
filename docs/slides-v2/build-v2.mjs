import { Resvg } from '@resvg/resvg-js';
import { PDFDocument } from 'pdf-lib';
import fs from 'node:fs';
import * as fontkit from 'fontkit';
import path from 'node:path';

const OUT_DIR = '/home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai/docs/slides-v2';
const PDF_PATH = '/home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai/docs/AIR-GB-Lightning-Pitch-Deck-v2.pdf';
const TOTAL = 9;

const C = {
  bg: '#F6F4EF',
  ink: '#15171E',
  muted: '#5E6472',
  faint: '#9AA0AB',
  line: '#E2DED4',
  card: '#FFFFFF',
  indigo: '#3B3FD8',
  indigoSoft: '#E9E9FB',
  teal: '#0E8A7A',
  tealSoft: '#E0F2EF',
  purple: '#7A3FD1',
  purpleSoft: '#F0E9FB',
  blue: '#1D6FD8',
  blueSoft: '#E5EFFC',
  gray: '#A3A8B2',
  graySoft: '#ECEAE4',
};

const FONT = 'Ubuntu Sans';

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const VAR_FONT = fontkit.openSync('/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf');
const FALLBACK = {
  regular: fontkit.openSync('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
  bold: fontkit.openSync('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
};
const instances = new Map();
function fontFor(weight) {
  if (!instances.has(weight)) instances.set(weight, VAR_FONT.getVariation({ wght: weight, wdth: 100 }));
  return instances.get(weight);
}

function segments(str, weight) {
  const main = fontFor(weight);
  const alt = weight >= 600 ? FALLBACK.bold : FALLBACK.regular;
  const out = [];
  for (const ch of str) {
    const font = VAR_FONT.hasGlyphForCodePoint(ch.codePointAt(0)) ? main : alt;
    const last = out[out.length - 1];
    if (last && last.font === font) last.text += ch;
    else out.push({ font, text: ch });
  }
  return out;
}

function shape(str, size, weight, spacing) {
  const glyphs = [];
  let pen = 0;
  for (const { font, text } of segments(str, weight)) {
    const s = size / font.unitsPerEm;
    const run = font.layout(text);
    run.glyphs.forEach((g, i) => {
      const p = run.positions[i];
      glyphs.push({ g, s, x: pen + p.xOffset * s, y: -p.yOffset * s });
      pen += p.xAdvance * s + spacing;
    });
  }
  return { glyphs, width: pen - (glyphs.length ? spacing : 0) };
}

function textWidth(str, size = 28, weight = 400, spacing = 0) {
  return shape(str, size, weight, spacing).width;
}

function t(x, y, str, o = {}) {
  const { size = 28, weight = 400, fill = C.ink, anchor = 'start', spacing = 0 } = o;
  if (!str) return '';
  const { glyphs, width } = shape(str, size, weight, spacing);
  const x0 = anchor === 'middle' ? x - width / 2 : anchor === 'end' ? x - width : x;
  const d = glyphs
    .map(({ g, s, x: gx, y: gy }) => g.path.transform(s, 0, 0, -s, x0 + gx, y + gy).toSVG())
    .filter(Boolean)
    .join('')
    .replace(/(\d+\.\d{2})\d+/g, '$1');
  return `<path aria-label="${esc(str)}" fill="${fill}" d="${d}"/>`;
}

function lines(x, y, arr, o = {}) {
  const lh = o.lh ?? Math.round((o.size ?? 28) * 1.35);
  return arr.map((s, i) => t(x, y + i * lh, s, o)).join('\n');
}

function rect(x, y, w, h, o = {}) {
  const { rx = 20, fill = C.card, stroke = 'none', sw = 0, shadow = false, dash = '' } = o;
  const f = shadow ? ' filter="url(#shadow)"' : '';
  const d = dash ? ` stroke-dasharray="${dash}"` : '';
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"${d}${f}/>`;
}

function card(x, y, w, h, o = {}) {
  return rect(x, y, w, h, { fill: C.card, stroke: C.line, sw: 2, shadow: true, ...o });
}

function arrow(x1, y1, x2, y2, o = {}) {
  const { color = C.ink, w = 4, head = 20 } = o;
  const a = Math.atan2(y2 - y1, x2 - x1);
  const bx = x2 - head * Math.cos(a);
  const by = y2 - head * Math.sin(a);
  const hw = head * 0.6;
  const p1 = [bx + hw * Math.sin(a), by - hw * Math.cos(a)];
  const p2 = [bx - hw * Math.sin(a), by + hw * Math.cos(a)];
  return `<line x1="${x1}" y1="${y1}" x2="${bx.toFixed(1)}" y2="${by.toFixed(1)}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"/>
<polygon points="${x2},${y2} ${p1[0].toFixed(1)},${p1[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}" fill="${color}"/>`;
}

function circle(cx, cy, r, o = {}) {
  const { fill = C.card, stroke = C.ink, sw = 3 } = o;
  return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
}

function pill(x, y, label, color, soft) {
  const w = Math.round(textWidth(label, 20, 700, 2) + 48);
  return `${rect(x, y, w, 40, { rx: 20, fill: soft })}
${t(x + w / 2, y + 28, label, { size: 20, weight: 700, fill: color, anchor: 'middle', spacing: 2 })}`;
}

function frame(n, kicker, title, body) {
  const header = kicker
    ? `${rect(120, 118, 56, 6, { rx: 3, fill: C.indigo })}
${t(196, 128, kicker, { size: 22, weight: 700, fill: C.indigo, spacing: 3 })}
${t(120, 222, title, { size: 60, weight: 700 })}`
    : '';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
<defs>
  <filter id="shadow" x="-10%" y="-10%" width="120%" height="130%">
    <feDropShadow dx="0" dy="6" stdDeviation="10" flood-color="#15171E" flood-opacity="0.07"/>
  </filter>
</defs>
<rect width="1920" height="1080" fill="${C.bg}"/>
${header}
${body}
${t(120, 1040, 'AIR-GB Lightning', { size: 20, weight: 500, fill: C.faint })}
${t(1800, 1040, `${n} / ${TOTAL}`, { size: 20, weight: 500, fill: C.faint, anchor: 'end' })}
</svg>`;
}

// ---------- Slide 1: Title ----------
function slide1() {
  const kpis = [
    ['35% → 65%', 'payment success, 20 scenarios'],
    ['−79%', 'fees on payments both completed'],
    ['< 1.4 ms', 'route planning on the node'],
  ];
  const kpiSvg = kpis
    .map(([v, l], i) => {
      const x = 120 + i * 580;
      const sep = i > 0 ? `<line x1="${x - 40}" y1="700" x2="${x - 40}" y2="860" stroke="${C.line}" stroke-width="3"/>` : '';
      return `${sep}
${t(x, 790, v, { size: 88, weight: 700, fill: C.indigo })}
${t(x, 846, l, { size: 28, fill: C.muted })}`;
    })
    .join('\n');
  return frame(
    1,
    null,
    null,
    `${rect(120, 250, 96, 10, { rx: 5, fill: C.indigo })}
${t(112, 420, 'AIR-GB Lightning', { size: 128, weight: 700 })}
${t(120, 505, 'Multi-path, intent-aware routing for RGB assets', { size: 46, weight: 500, fill: C.ink })}
${t(120, 570, 'Local AI proposes  ·  deterministic code verifies  ·  Rust routes', { size: 30, fill: C.muted })}
${kpiSvg}
${t(120, 960, 'Built on rgb-lightning-node  ·  rust-lightning  ·  QVAC local AI', { size: 24, fill: C.faint })}`,
  );
}

// ---------- Slide 2: Opportunity ----------
function slide2() {
  const ax = 250, jx = 810, ny = 620;
  const diagram = `${card(120, 300, 820, 640)}
${t(530, 385, 'Alice pays Judy 100 RGB', { size: 32, weight: 700, anchor: 'middle' })}
<path d="M ${ax + 52} ${ny - 30} Q 530 400 ${jx - 52} ${ny - 30}" fill="none" stroke="${C.indigo}" stroke-width="8" stroke-linecap="round"/>
<path d="M ${ax + 52} ${ny + 30} Q 530 840 ${jx - 52} ${ny + 30}" fill="none" stroke="${C.teal}" stroke-width="8" stroke-linecap="round"/>
${rect(460, 487, 140, 48, { rx: 24, fill: C.indigoSoft })}
${t(530, 520, '60 RGB', { size: 26, weight: 700, fill: C.indigo, anchor: 'middle' })}
${rect(460, 705, 140, 48, { rx: 24, fill: C.tealSoft })}
${t(530, 738, '40 RGB', { size: 26, weight: 700, fill: C.teal, anchor: 'middle' })}
${circle(ax, ny, 62, { stroke: C.ink, sw: 4 })}
${t(ax, ny + 10, 'Alice', { size: 28, weight: 700, anchor: 'middle' })}
${circle(jx, ny, 62, { stroke: C.ink, sw: 4 })}
${t(jx, ny + 10, 'Judy', { size: 28, weight: 700, anchor: 'middle' })}
${t(530, 850, 'One path carries at most 60 RGB.', { size: 28, fill: C.muted, anchor: 'middle' })}
${t(530, 896, 'Two paths deliver the full 100.', { size: 28, weight: 700, fill: C.indigo, anchor: 'middle' })}`;

  const items = [
    ['Combine liquidity', ['Split one payment across', 'parallel channels.']],
    ['Keep channels balanced', ['Return flows refill channels,', 'so later payments stay direct.']],
    ['Follow user intent', ['Fee caps, nodes to avoid, privacy,', 'written in plain language.']],
  ];
  const right = items
    .map(([title, desc], i) => {
      const y = 380 + i * 200;
      return `${circle(1060, y - 12, 32, { fill: C.indigo, stroke: C.indigo, sw: 0 })}
${t(1060, y, String(i + 1), { size: 30, weight: 700, fill: '#FFFFFF', anchor: 'middle' })}
${t(1125, y, title, { size: 38, weight: 700 })}
${lines(1125, y + 50, desc, { size: 28, fill: C.muted, lh: 40 })}`;
    })
    .join('\n');

  return frame(2, 'THE OPPORTUNITY', 'Why multi-path matters as RGB Lightning grows', diagram + '\n' + right);
}

// ---------- Slide 3: Architecture ----------
function slide3() {
  const W = 375, G = 60, X0 = 120;
  const slot = (i) => X0 + i * (W + G);

  const box = (x, y, w, h, title, sub, o = {}) => {
    const { accent = C.ink, num = null, fill = C.card, stroke = C.line } = o;
    const cx = x + w / 2;
    const numSvg = num ? t(x + 28, y + 46, num, { size: 24, weight: 700, fill: accent }) : '';
    const titleY = sub ? y + h / 2 + 2 : y + h / 2 + 11;
    return `${rect(x, y, w, h, { fill, stroke, sw: 2, shadow: true })}
${numSvg}
${t(cx, titleY, title, { size: 30, weight: 700, anchor: 'middle', fill: C.ink })}
${sub ? t(cx, titleY + 42, sub, { size: 25, fill: C.muted, anchor: 'middle' }) : ''}`;
  };

  const by = 430, bh = 130;
  const baseline = `${t(120, 400, 'Baseline', { size: 32, weight: 700, fill: C.muted })}
${t(270, 400, 'stock rgb-lightning-node', { size: 24, fill: C.faint })}
${t(1800, 400, '7 / 20 succeeded', { size: 32, weight: 700, fill: C.muted, anchor: 'end' })}
${box(slot(0), by, W, bh, 'Payment request', null, { fill: C.graySoft, stroke: C.graySoft })}
${arrow(slot(0) + W + 10, by + bh / 2, slot(1) - 10, by + bh / 2, { color: C.gray })}
${box(slot(1), by, W * 2 + G, bh, 'One shortest path', null, { fill: C.graySoft, stroke: C.graySoft })}
${arrow(slot(2) + W + 10, by + bh / 2, slot(3) - 10, by + bh / 2, { color: C.gray })}
${box(slot(3), by, W, bh, 'Send', null, { fill: C.graySoft, stroke: C.graySoft })}`;

  const ay = 720, ah = 190;
  const stages = [
    ['Understand intent', 'plain-language policy'],
    ['Recall & score', 'local AI + payment memory'],
    ['Verify & choose', 'guardrails pick a strategy'],
    ['Plan in Rust', 'multi-path graph planners'],
  ];
  const airgb = `${t(120, 690, 'AIR-GB', { size: 32, weight: 700, fill: C.indigo })}
${t(1800, 690, '13 / 20 succeeded', { size: 32, weight: 700, fill: C.indigo, anchor: 'end' })}
${stages
  .map(([title, sub], i) => {
    const a = i < 3 ? arrow(slot(i) + W + 10, ay + ah / 2, slot(i + 1) - 10, ay + ah / 2, { color: C.indigo }) : '';
    return `${box(slot(i), ay, W, ah, title, sub, { accent: C.indigo, num: String(i + 1), stroke: C.indigo })}\n${a}`;
  })
  .join('\n')}`;

  return frame(3, 'ARCHITECTURE', 'How a payment gets routed', baseline + '\n' + airgb);
}

// ---------- Slide 4: Models ----------
function slide4() {
  const cy = 320, ch = 480, cw = 700;
  const modelCard = (x, tag, color, soft, name, spec, bullets) => `${card(x, cy, cw, ch)}
${rect(x, cy, 12, ch, { rx: 6, fill: color })}
${pill(x + 60, cy + 50, tag, color, soft)}
${t(x + 60, cy + 160, name, { size: 48, weight: 700 })}
${t(x + 60, cy + 205, spec, { size: 26, fill: C.muted })}
${bullets
  .map((b, i) => {
    const y = cy + 290 + i * 62;
    return `${circle(x + 72, y - 10, 7, { fill: color, stroke: color, sw: 0 })}
${t(x + 100, y, b, { size: 30 })}`;
  })
  .join('\n')}`;

  const left = modelCard(120, 'MEMORY', C.purple, C.purpleSoft, 'EmbeddingGemma', '300M parameters  ·  4-bit', [
    'Saves every payment outcome',
    'Recalls the 4 closest matches',
    'Runs before each payment',
  ]);
  const right = modelCard(1100, 'REASONING', C.blue, C.blueSoft, 'Qwen3.5', '0.8B parameters  ·  4-bit  ·  temperature 0', [
    'Reads the routing request',
    'Scores each channel',
    'Suggests algorithm and split',
  ]);
  const midY = cy + ch / 2;
  const link = `${t(960, midY - 30, 'past payments', { size: 24, weight: 600, fill: C.purple, anchor: 'middle' })}
${arrow(845, midY, 1075, midY, { color: C.purple, w: 6, head: 26 })}
${t(960, midY + 52, 'top 4 matches', { size: 24, fill: C.muted, anchor: 'middle' })}`;

  const strip = `${rect(120, 860, 1680, 96, { rx: 48, fill: C.indigoSoft })}
${t(960, 920, '100% on the node   ·   no cloud calls   ·   no keys   ·   only the node’s own data', { size: 30, weight: 600, fill: C.indigo, anchor: 'middle' })}`;

  return frame(4, 'LOCAL AI', 'Two small local models, one job each', left + '\n' + right + '\n' + link + '\n' + strip);
}

// ---------- Slide 5: Guardrails ----------
function slide5() {
  const W = 390, G = 40, y = 400, h = 400;
  const gates = [
    ['Locked output', ['The model can only', 'name channels that', 'really exist.']],
    ['Grounded in words', ['Fees and nodes must', 'appear in the user’s', 'own request.']],
    ['Real balances win', ['Live balances', 'override the model’s', 'channel scores.']],
    ['Fixed strategy rules', ['Split when one path', 'can’t fit; one path', 'when privacy is asked.']],
  ];
  const flow = `${t(120, 345, 'Model output', { size: 26, weight: 600, fill: C.muted })}
${arrow(310, 336, 1580, 336, { color: C.gray, w: 3, head: 18 })}
${t(1800, 345, 'Payment sent', { size: 26, weight: 600, fill: C.muted, anchor: 'end' })}`;
  const cards = gates
    .map(([title, desc], i) => {
      const x = 120 + i * (W + G);
      return `${card(x, y, W, h)}
${t(x + 40, y + 100, String(i + 1), { size: 72, weight: 700, fill: C.indigo })}
${t(x + 40, y + 180, title, { size: 33, weight: 700 })}
${lines(x + 40, y + 240, desc, { size: 27, fill: C.muted, lh: 40 })}`;
    })
    .join('\n');
  const bottom = t(960, 905, 'A model mistake is caught before any payment is sent.', { size: 38, weight: 700, fill: C.ink, anchor: 'middle' });
  return frame(5, 'GUARDRAILS', 'The AI suggests. Code decides.', flow + '\n' + cards + '\n' + bottom);
}

// ---------- Slide 6: MPP ----------
function slide6() {
  const A = [220, 520], J = [1700, 520];
  const topY = 380, botY = 660;
  const xs = [620, 960, 1300];
  const top = ['Bob', 'Erin', 'Heidi'];
  const bot = ['Carol', 'Frank', 'Ivan'];
  const seg = (pts, color) =>
    `<polyline points="${pts.map((p) => p.join(',')).join(' ')}" fill="none" stroke="${color}" stroke-width="8" stroke-linejoin="round" stroke-linecap="round"/>`;
  const topPts = [A, ...xs.map((x) => [x, topY]), J];
  const botPts = [A, ...xs.map((x) => [x, botY]), J];
  const hop = (x, y, name, color) => `${circle(x, y, 44, { stroke: color, sw: 4 })}
${t(x, y + 8, name, { size: 23, weight: 700, anchor: 'middle' })}`;
  const diagram = `${seg(topPts, C.indigo)}
${seg(botPts, C.teal)}
${rect(720, topY - 26, 140, 52, { rx: 26, fill: C.indigo })}
${t(790, topY + 10, '60 RGB', { size: 26, weight: 700, fill: '#FFFFFF', anchor: 'middle' })}
${rect(720, botY - 26, 140, 52, { rx: 26, fill: C.teal })}
${t(790, botY + 10, '40 RGB', { size: 26, weight: 700, fill: '#FFFFFF', anchor: 'middle' })}
${xs.map((x, i) => hop(x, topY, top[i], C.indigo)).join('\n')}
${xs.map((x, i) => hop(x, botY, bot[i], C.teal)).join('\n')}
${circle(A[0], A[1], 66, { stroke: C.ink, sw: 4 })}
${t(A[0], A[1] + 10, 'Alice', { size: 30, weight: 700, anchor: 'middle' })}
${t(A[0], A[1] + 115, 'sends 100', { size: 26, fill: C.muted, anchor: 'middle' })}
${circle(J[0], J[1], 66, { stroke: C.ink, sw: 4 })}
${t(J[0], J[1] + 10, 'Judy', { size: 30, weight: 700, anchor: 'middle' })}
${t(J[0], J[1] + 115, 'receives 100', { size: 26, fill: C.muted, anchor: 'middle' })}`;

  const steps = [
    ['Check capacity', 'Which channels can carry the RGB?'],
    ['Split by cost', 'Cheaper path first, then the rest'],
    ['Settle atomically', 'Judy claims only when all 100 arrive'],
  ];
  const stepSvg = steps
    .map(([title, desc], i) => {
      const x = 120 + i * 580;
      return `${t(x, 850, String(i + 1), { size: 44, weight: 700, fill: C.indigo })}
${t(x + 50, 850, title, { size: 34, weight: 700 })}
${t(x + 50, 900, desc, { size: 26, fill: C.muted })}`;
    })
    .join('\n');
  const divider = `<line x1="120" y1="775" x2="1800" y2="775" stroke="${C.line}" stroke-width="3"/>`;
  return frame(6, 'MULTI-PATH PAYMENTS', 'One payment, two paths, settled together', diagram + '\n' + divider + '\n' + stepSvg);
}

// ---------- Slide 7: Topology ----------
function slide7() {
  const N = {
    Alice: [200, 600],
    Bob: [470, 420],
    Carol: [470, 600],
    Dave: [470, 800],
    Erin: [770, 420],
    Frank: [770, 600],
    Grace: [990, 820],
    Heidi: [1070, 420],
    Ivan: [1070, 600],
    Judy: [1290, 510],
  };
  const edge = (a, b, color, w, dash = '') =>
    `<line x1="${N[a][0]}" y1="${N[a][1]}" x2="${N[b][0]}" y2="${N[b][1]}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ''}/>`;
  const cross = [['Frank', 'Bob'], ['Erin', 'Carol'], ['Frank', 'Dave'], ['Heidi', 'Frank']];
  const other = [['Alice', 'Dave'], ['Dave', 'Grace'], ['Ivan', 'Grace']];
  const c60 = [['Alice', 'Bob'], ['Bob', 'Erin'], ['Erin', 'Heidi'], ['Heidi', 'Judy']];
  const c40 = [['Alice', 'Carol'], ['Carol', 'Frank'], ['Frank', 'Ivan'], ['Ivan', 'Judy']];
  const graph = `${cross.map(([a, b]) => edge(a, b, C.gray, 3, '10 10')).join('\n')}
${other.map(([a, b]) => edge(a, b, C.gray, 5)).join('\n')}
${c60.map(([a, b]) => edge(a, b, C.indigo, 9)).join('\n')}
${c40.map(([a, b]) => edge(a, b, C.teal, 9)).join('\n')}
${Object.entries(N)
  .map(([name, [x, y]]) => {
    const big = name === 'Alice' || name === 'Judy';
    return `${circle(x, y, big ? 56 : 48, { stroke: C.ink, sw: big ? 4 : 3 })}
${t(x, y + 8, name, { size: big ? 26 : 22, weight: 700, anchor: 'middle' })}`;
  })
  .join('\n')}`;

  const lx = 1440;
  const legendRow = (y, color, w, dash, label) =>
    `<line x1="${lx}" y1="${y - 9}" x2="${lx + 60}" y2="${y - 9}" stroke="${color}" stroke-width="${w}" stroke-linecap="round"${dash ? ` stroke-dasharray="${dash}"` : ''}/>
${t(lx + 85, y, label, { size: 26 })}`;
  const legend = `${legendRow(400, C.indigo, 9, '', '60 RGB corridor')}
${legendRow(455, C.teal, 9, '', '40 RGB corridor')}
${legendRow(510, C.gray, 5, '', 'other channels')}
${legendRow(565, C.gray, 3, '10 10', 'high-fee cross links')}
<line x1="${lx}" y1="625" x2="1800" y2="625" stroke="${C.line}" stroke-width="3"/>
${lines(lx, 690, ['+ 10 isolated nodes', 'as negative controls'], { size: 26, weight: 600, lh: 38 })}
${lines(lx, 800, ['Same starting state', 'for both runs'], { size: 26, weight: 600, lh: 38 })}
${lines(lx, 910, ['20 payments,', 'run in sequence'], { size: 26, weight: 600, lh: 38 })}`;

  return frame(7, 'BENCHMARK SETUP', 'Test network: 20 nodes, 15 RGB channels', graph + '\n' + legend);
}

// ---------- Slide 8: Results ----------
function slide8() {
  const bar = (x, y, w, color) => rect(x, y, w, 44, { rx: 10, fill: color });
  const success = `${card(120, 300, 820, 280)}
${t(160, 365, 'Payments completed', { size: 30, weight: 700 })}
${t(900, 365, '+30 pts', { size: 30, weight: 700, fill: C.indigo, anchor: 'end' })}
${t(160, 452, 'Baseline', { size: 26, fill: C.muted })}
${bar(330, 420, 35 * 5.85, C.gray)}
${t(330 + 35 * 5.85 + 20, 455, '7 / 20', { size: 30, weight: 700, fill: C.muted })}
${t(160, 532, 'AIR-GB', { size: 26, weight: 700, fill: C.indigo })}
${bar(330, 500, 65 * 5.85, C.indigo)}
${t(330 + 65 * 5.85 + 20, 535, '13 / 20', { size: 30, weight: 700, fill: C.indigo })}`;

  const feeScale = 330 / 5700;
  const fees = `${card(980, 300, 820, 280)}
${t(1020, 365, 'Fees, payments both completed', { size: 30, weight: 700 })}
${t(1760, 365, '−79%', { size: 30, weight: 700, fill: C.indigo, anchor: 'end' })}
${t(1020, 452, 'Baseline', { size: 26, fill: C.muted })}
${bar(1190, 420, 5700 * feeScale, C.gray)}
${t(1190 + 5700 * feeScale + 20, 455, '5,700 msat', { size: 30, weight: 700, fill: C.muted })}
${t(1020, 532, 'AIR-GB', { size: 26, weight: 700, fill: C.indigo })}
${bar(1190, 500, 1200 * feeScale, C.indigo)}
${t(1190 + 1200 * feeScale + 20, 535, '1,200 msat', { size: 30, weight: 700, fill: C.indigo })}`;

  const colB = 1280, colA = 1600;
  const rows = [
    ['Multi-path payments (60 + 40 RGB)', '0 / 3', '3 / 3'],
    ['RGB returned to the sender', '0', '190 RGB'],
    ['Mid-network payments', '0 / 3', '3 / 3'],
    ['Impossible payments rejected', '4 / 4', '4 / 4'],
  ];
  const table = `${card(120, 620, 1680, 370)}
${t(160, 682, 'WHERE THE GAIN COMES FROM', { size: 22, weight: 700, fill: C.faint, spacing: 2 })}
${t(colB, 682, 'BASELINE', { size: 22, weight: 700, fill: C.faint, anchor: 'middle', spacing: 2 })}
${t(colA, 682, 'AIR-GB', { size: 22, weight: 700, fill: C.indigo, anchor: 'middle', spacing: 2 })}
${rows
  .map(([label, b, a], i) => {
    const y = 760 + i * 66;
    return `<line x1="160" y1="${y - 46}" x2="1760" y2="${y - 46}" stroke="${C.line}" stroke-width="2"/>
${t(160, y, label, { size: 30 })}
${t(colB, y, b, { size: 32, weight: 700, fill: C.muted, anchor: 'middle' })}
${t(colA, y, a, { size: 32, weight: 700, fill: C.indigo, anchor: 'middle' })}`;
  })
  .join('\n')}`;

  return frame(8, 'RESULTS', 'Same network, same 20 payments', success + '\n' + fees + '\n' + table);
}

// ---------- Slide 9: Demo ----------
function slide9() {
  const steps = [
    ['Ask in plain words', ['“Max 1,000 msat fee,', 'and avoid Bob.”']],
    ['See the AI’s view', ['Channel scores from', 'local payment memory']],
    ['Send it', ['An RGB payment split', 'across two paths']],
  ];
  const cols = steps
    .map(([title, desc], i) => {
      const x = 120 + i * 580;
      return `${t(x, 430, String(i + 1), { size: 110, weight: 700, fill: C.indigo })}
${t(x, 510, title, { size: 40, weight: 700 })}
${lines(x, 565, desc, { size: 30, fill: C.muted, lh: 44 })}`;
    })
    .join('\n');
  const band = `${rect(120, 720, 1680, 130, { rx: 65, fill: C.indigo })}
${t(960, 800, '35% → 65% success   ·   −79% fees   ·   100% local AI', { size: 42, weight: 700, fill: '#FFFFFF', anchor: 'middle' })}
${t(960, 950, 'Thank you. Questions welcome.', { size: 34, weight: 500, fill: C.muted, anchor: 'middle' })}`;
  return frame(9, 'LIVE DEMO', 'From a sentence to a multi-path RGB payment', cols + '\n' + band);
}

const slides = [slide1, slide2, slide3, slide4, slide5, slide6, slide7, slide8, slide9];

fs.mkdirSync(OUT_DIR, { recursive: true });
const pdf = await PDFDocument.create();
for (const [i, make] of slides.entries()) {
  const name = `slide-${String(i + 1).padStart(2, '0')}`;
  const svg = make();
  fs.writeFileSync(path.join(OUT_DIR, `${name}.svg`), svg);
  const png = new Resvg(svg, {
    fitTo: { mode: 'width', value: 2560 },
    font: { loadSystemFonts: true, defaultFontFamily: FONT },
  })
    .render()
    .asPng();
  fs.writeFileSync(path.join(OUT_DIR, `${name}.png`), png);
  const img = await pdf.embedPng(png);
  const page = pdf.addPage([1920, 1080]);
  page.drawImage(img, { x: 0, y: 0, width: 1920, height: 1080 });
}
pdf.setTitle('AIR-GB Lightning — Pitch Deck v2');
fs.writeFileSync(PDF_PATH, await pdf.save());
console.log('done', PDF_PATH);
