import { parseArgs } from 'node:util';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readJson, save, check } from './common.mjs';

export function compareRuns(ai, baseline, aiRows, baseRows) {
  const problems = [];
  if (ai.schemaVersion !== 1 || baseline.schemaVersion !== 1) problems.push('schema_version');
  if (ai.mode !== 'ai' || baseline.mode !== 'baseline') problems.push('run_modes');
  for (const field of ['suiteHash', 'topologyHash', 'initialStateHash', 'execution']) {
    if (!ai[field] || ai[field] !== baseline[field]) problems.push(field);
  }
  if (JSON.stringify(ai.timeouts) !== JSON.stringify(baseline.timeouts)) problems.push('timeouts');
  if (ai.status !== 'completed' || baseline.status !== 'completed') problems.push('incomplete_run');
  const unique = rows => new Set(rows.map(r => r.caseId)).size === rows.length;
  if (aiRows.length !== 20 || baseRows.length !== 20 || !unique(aiRows) || !unique(baseRows)) problems.push('case_count_or_duplicates');
  const valid = new Set(['succeeded', 'failed']);
  if ([...aiRows, ...baseRows].some(r => !valid.has(r.status))) problems.push('harness_errors_or_unresolved_payments');
  if (baseRows.some(r => r.metrics?.actualMpp === true)) problems.push('baseline_used_mpp');
  if ([...aiRows, ...baseRows].some(r => r.logErrors?.length) || ai.finalLogErrors?.length || baseline.finalLogErrors?.length) problems.push('missing_node_logs');
  if ([...aiRows, ...baseRows].some(r => r.errors?.some(e => e.stage === 'snapshot_after'))) problems.push('missing_final_snapshots');
  const baseMap = new Map(baseRows.map(row => [row.caseId, row]));
  const pairs = aiRows.map(a => {
    const b = baseMap.get(a.caseId);
    if (!b || a.caseHash !== b.caseHash) { problems.push(`case_mismatch:${a.caseId}`); return { caseId: a.caseId, comparable: false }; }
    const bothSucceeded = a.status === 'succeeded' && b.status === 'succeeded';
    const knownFees = bothSucceeded && [a.metrics?.totalFeeMsat, b.metrics?.totalFeeMsat].every(v => typeof v === 'string' && /^\d+$/.test(v));
    const completePaths = bothSucceeded && a.metrics?.complete && b.metrics?.complete;
    const latency = key => bothSucceeded && [a[key], b[key]].every(v => typeof v === 'number' && Number.isFinite(v)) ? a[key] - b[key] : null;
    return { caseId: a.caseId, comparable: true, aiStatus: a.status, baselineStatus: b.status, bothSucceeded,
      algorithm: a.decision?.recommendedRouting?.algorithm ?? null, requestedMaxPaths: a.decision?.recommendedRouting?.max_paths ?? null,
      aiMpp: a.metrics?.actualMpp ?? null, baselineMpp: b.metrics?.actualMpp ?? null,
      aiFeeMsat: knownFees ? a.metrics.totalFeeMsat : null, baselineFeeMsat: knownFees ? b.metrics.totalFeeMsat : null,
      feeDeltaMsat: knownFees ? String(BigInt(a.metrics.totalFeeMsat) - BigInt(b.metrics.totalFeeMsat)) : null,
      hopDelta: completePaths ? a.metrics.totalHops - b.metrics.totalHops : null,
      selectionMs: a.selectionMs, settlementDeltaMs: latency('settlementMs'), endToEndDeltaMs: latency('endToEndMs'),
      sameStateBeforePayment: a.beforeStateHash === b.beforeStateHash,
    };
  });
  const paired = pairs.filter(p => p.bothSucceeded);
  const feePairs = paired.filter(p => p.feeDeltaMsat !== null);
  const sum = (list, key) => list.reduce((n, p) => n + BigInt(p[key]), 0n);
  const aiFee = sum(feePairs, 'aiFeeMsat'), baseFee = sum(feePairs, 'baselineFeeMsat');
  const mean = (rows, key) => { const list = rows.map(r => r[key]).filter(v => typeof v === 'number' && Number.isFinite(v));
    return { sampleCount: list.length, value: list.length ? list.reduce((a, b) => a + b, 0) / list.length : null }; };
  const count = (rows, status) => rows.filter(r => r.status === status).length;
  const comparable = problems.length === 0;
  return { schemaVersion: 1, comparable, problems: [...new Set(problems)],
    unverifiedControls: ['Actual forwarding fee policies must be identical; snapshots do not expose them', 'Exclusive use of nodes during both runs', 'Baseline commit and disabled MPP must match the deployed backend'],
    interpretation: 'Deltas are AI minus baseline; negative fee/latency/hops is better. Sequential runs can diverge after the first payment. Success-rate deltas include all 20 cases; costs use only matched successes. No statistical significance is inferred from one run.',
    latencyComparable: comparable && ai.label !== null && ai.label === baseline.label,
    ai: { succeeded: count(aiRows, 'succeeded'), failed: count(aiRows, 'failed'), completeMeasurements: aiRows.filter(r => r.metrics?.complete).length },
    baseline: { succeeded: count(baseRows, 'succeeded'), failed: count(baseRows, 'failed'), completeMeasurements: baseRows.filter(r => r.metrics?.complete).length },
    successRateDeltaPercentagePoints: comparable ? (count(aiRows, 'succeeded') - count(baseRows, 'succeeded')) / 20 * 100 : null,
    aiOnlySuccesses: pairs.filter(p => p.aiStatus === 'succeeded' && p.baselineStatus === 'failed').length,
    baselineOnlySuccesses: pairs.filter(p => p.aiStatus === 'failed' && p.baselineStatus === 'succeeded').length,
    pairedSuccesses: paired.length,
    fees: { sampleCount: feePairs.length, aiTotalMsat: feePairs.length ? String(aiFee) : null, baselineTotalMsat: feePairs.length ? String(baseFee) : null,
      deltaMsat: feePairs.length ? String(aiFee - baseFee) : null,
      savingPct: baseFee > 0n ? Number((baseFee - aiFee) * 10000n / baseFee) / 100 : null },
    settlementDeltaMs: mean(paired, 'settlementDeltaMs'), endToEndDeltaMs: mean(paired, 'endToEndDeltaMs'),
    hopDelta: mean(paired, 'hopDelta'), pairs,
    missingNodeLogs: [...aiRows, ...baseRows].filter(r => r.logErrors?.length).length,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: { ai: { type: 'string' }, baseline: { type: 'string' }, out: { type: 'string' } } });
  check(values.ai && values.baseline && values.out, 'Usage: --ai AI_DIR --baseline BASELINE_DIR --out REPORT.json');
  const rows = async dir => (await readFile(join(dir, 'results.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
  const report = compareRuns(await readJson(join(values.ai, 'manifest.json')), await readJson(join(values.baseline, 'manifest.json')),
    await rows(values.ai), await rows(values.baseline));
  await save(resolve(values.out), report);
  const columns = ['caseId', 'aiStatus', 'baselineStatus', 'algorithm', 'requestedMaxPaths', 'aiMpp', 'aiFeeMsat', 'baselineFeeMsat', 'feeDeltaMsat', 'hopDelta', 'settlementDeltaMs', 'endToEndDeltaMs'];
  const csv = value => `"${String(value ?? '').replaceAll('"', '""')}"`;
  await writeFile(`${resolve(values.out)}.csv`, [columns.join(','), ...report.pairs.map(row => columns.map(c => csv(row[c])).join(','))].join('\n') + '\n', { mode: 0o600 });
  console.log(JSON.stringify({ report: resolve(values.out), comparable: report.comparable, problems: report.problems, fees: report.fees }, null, 2));
  if (!report.comparable) process.exitCode = 1;
}
