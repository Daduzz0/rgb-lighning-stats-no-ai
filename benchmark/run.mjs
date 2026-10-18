import { parseArgs } from 'node:util';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readJson, save, append, hash, check, pause, validateSuite, comparableState } from './common.mjs';
import { createStockAdapter, measurements } from './adapters.mjs';
import { verifyInputs, requireReference } from './reference.mjs';
import { LogCollector } from './logs.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const { values } = parseArgs({ options: {
  mode: { type: 'string', default: 'baseline' }, config: { type: 'string' }, out: { type: 'string' },
  suite: { type: 'string', default: fileURLToPath(new URL('./scenarios.json', import.meta.url)) },
  topology: { type: 'string', default: join(root, 'benchmark/topology.json') }, reference: { type: 'string' },
  help: { type: 'boolean', default: false },
} });
if (values.help) {
  console.log('node benchmark/run.mjs --config state/rgb-stats-benchmark/benchmark.json --reference AIRGB_RUN_DIRECTORY [--out NEW_DIRECTORY]');
} else {
  check(values.config, '--config is required');
  check(values.mode === 'baseline', 'This runner only executes the stock single-path baseline');
  const inputLock = await verifyInputs();
  const [suite, topology, config] = await Promise.all([readJson(values.suite), readJson(values.topology), readJson(values.config)]);
  validateSuite(suite, topology, config);
  const defaultTopology = fileURLToPath(new URL('./topology.json', import.meta.url));
  check(hash(suite) === hash(await readJson(new URL('./scenarios.json', import.meta.url)))
    && (resolve(values.topology) !== resolve(defaultTopology) || hash(topology) === hash(await readJson(defaultTopology))), 'Use the exact locked AIR-GB inputs');
  const runId = randomUUID();
  const output = resolve(values.out ?? join(root, 'results/benchmark', `${values.mode}-${runId}`));
  // mkdir without recursive prevents overwriting or accidentally resuming payments.
  await mkdir(resolve(output, '..'), { recursive: true, mode: 0o700 });
  await mkdir(output, { mode: 0o700 });
  const collector = new LogCollector({ topology, config, output });
  const adapter = createStockAdapter({ topology, config, output, mode: values.mode, runId, collector });
  let stop = false;
  process.once('SIGINT', () => { stop = true; });
  process.once('SIGTERM', () => { stop = true; });
  const rows = [];
  const eventsPath = join(output, 'events.jsonl');
  const event = async (kind, data = {}) => {
    const entry = { timestamp: new Date().toISOString(), runId, kind, ...data };
    await append(eventsPath, entry); console.log(JSON.stringify(entry));
  };
  const manifest = { schemaVersion: 1, runId, mode: values.mode, suiteHash: hash(suite), topologyHash: hash(topology),
    label: config.label ?? null, startedAt: new Date().toISOString(), status: 'starting',
    execution: 'sequential-live', nodeVersion: process.version, platform: process.platform, arch: process.arch,
    // No passwords, environment dump or authentication headers are persisted.
    assetAliases: config.assets, timeouts: { paymentMs: config.paymentTimeoutMs ?? 180000,
      observationMs: config.observationTimeoutMs ?? 15000, requestMs: config.requestTimeoutMs ?? 120000 },
  };
  try {
    try { manifest.repoCommit = (await promisify(execFile)('git', ['rev-parse', 'HEAD'], { cwd: root })).stdout.trim(); }
    catch { manifest.repoCommit = null; }
    manifest.nativeBuild = null;
    manifest.inputsLock = inputLock;
    manifest.backend = await adapter.metadata();
    check(manifest.backend.mode === values.mode, 'Adapter mode does not match the requested run');
    const initial = await adapter.snapshot();
    manifest.initialStateHash = hash(comparableState(initial, config.assets));
    manifest.initialState = comparableState(initial, config.assets);
    await save(join(output, 'initial-state.json'), initial);
    await save(join(output, 'suite.json'), suite);
    await save(join(output, 'topology.json'), topology);
    manifest.reference = values.reference
      ? await requireReference(resolve(values.reference), manifest, initial, config, output)
      : null;
    const logErrors = await collector.all();
    check(logErrors.length === 0, `Log collection preflight failed: ${JSON.stringify(logErrors)}`);
    manifest.runnerSourceHashes = {};
    for (const file of ['run.mjs', 'common.mjs', 'logs.mjs', 'adapters.mjs', 'compare.mjs', 'reference.mjs', 'setup.py']) {
      manifest.runnerSourceHashes[file] = hash(await readFile(new URL(file, import.meta.url), 'utf8'));
    }
    for (const file of ['Dockerfile.benchmark', 'single_path.py', 'instrument.py', 'upstream-lock.json']) {
      manifest.runnerSourceHashes[`docker/${file}`] = hash(await readFile(join(root, 'docker', file), 'utf8'));
    }
    manifest.status = 'running';
    await save(join(output, 'manifest.json'), manifest);
    await event('started', { output, transactions: suite.cases.length });
    for (const c of suite.cases) {
      if (stop) break;
      const directory = join(output, 'transactions', c.id);
      const record = { schemaVersion: 1, runId, caseId: c.id, caseHash: hash(c), payment: c,
        status: 'harness_error', submitted: false, startedAt: new Date().toISOString(),
        selectionMs: null, settlementMs: null, endToEndMs: null, metrics: null, errors: [], logErrors: [] };
      record.logStartCursors = structuredClone(collector.cursors);
      const caseStarted = performance.now();
      let handle;
      let before;
      try {
        before = await adapter.snapshot();
        await save(join(directory, 'before.json'), before);
        record.beforeStateHash = hash(comparableState(before, config.assets));
        if (rows.length === 0 && record.beforeStateHash !== manifest.initialStateHash) {
          stop = true;
          throw new Error('Initial liquidity changed during preflight; no payments submitted');
        }
        const invoice = await adapter.invoice(c);
        check(typeof invoice === 'string' && invoice.startsWith('lnbcrt'), 'Missing regtest invoice');
        const selectionStarted = performance.now();
        record.decision = await adapter.select(c, invoice, before);
        record.selectionMs = Math.round(performance.now() - selectionStarted);
        record.selectionApplicable = false;
        await save(join(directory, 'decision.json'), record.decision);
        handle = await adapter.prepare(c, invoice, record.decision, `bench-${runId}-${c.id}`);
        check(/^[0-9a-f]{64}$/.test(handle.paymentHash), 'Missing payment hash');
        record.paymentHash = handle.paymentHash;
        record.intentId = handle.id;
        await save(join(directory, 'intent.json'), handle);
        // Persist the hash and intent before the one and only submission.
        await save(join(directory, 'result.json'), record);
        await event('selected', { caseId: c.id, routing: record.decision.recommendedRouting });
        check(!stop, 'Interrupted before submission');
        const sendStarted = performance.now();
        record.submitted = true;
        await save(join(directory, 'result.json'), record);
        try { await adapter.send(c, handle); }
        catch (error) { record.errors.push({ stage: 'submission_response', message: error.message }); }
        const deadline = sendStarted + (config.paymentTimeoutMs ?? 180000);
        record.status = 'timeout_unknown';
        // Reconcile even after an HTTP timeout. Never retry send/confirm.
        do {
          try {
            const status = await adapter.status(c, handle);
            await append(join(directory, 'status.jsonl'), { timestamp: Date.now(), ...status });
            record.lastStatus = status;
            if (status.sender === 'succeeded' && status.receiver === 'succeeded') { record.status = 'succeeded'; break; }
            if (status.sender === 'failed' && status.receiver !== 'succeeded') { record.status = 'failed'; break; }
            if (status.sender === 'failed' && status.receiver === 'succeeded') { record.status = 'inconsistent'; break; }
          } catch (error) { record.lastPollError = error.message; }
          if (stop || performance.now() >= deadline) break;
          await pause(1000);
        } while (true);
        record.settlementMs = Math.round(performance.now() - sendStarted);
        record.endToEndMs = Math.round(performance.now() - caseStarted);
        const aliases = Object.fromEntries(Object.entries(before).map(([id, row]) => [row.nodeId, id]));
        const observationDeadline = Date.now() + (config.observationTimeoutMs ?? 15000);
        do {
          await collector.collect(c.sender);
          const observations = adapter.observations ? await adapter.observations(c, handle) : await collector.events(c.sender);
          record.metrics = measurements(observations, handle.paymentHash, c, aliases);
          record.failureReason = observations.filter(e => e.paymentHash === handle.paymentHash && e.kind === 'payment_failed').at(-1)?.error ?? null;
          if (record.status !== 'succeeded' || record.metrics.complete || stop || Date.now() >= observationDeadline) break;
          await pause(500);
        } while (true);
        if (record.metrics.actualMpp === true) {
          record.errors.push({ stage: 'baseline', message: 'Observed MPP invalidates the stock single-path baseline' });
          record.status = 'invalid_baseline'; stop = true;
        }
        if (record.status === 'succeeded' && !record.metrics.complete) {
          record.errors.push({ stage: 'observability', message: 'Payment succeeded but settled fees or complete paths are missing; never substitute budgets or planned routes' });
        }
        if (['timeout_unknown', 'inconsistent'].includes(record.status)) stop = true;
      } catch (error) {
        record.errors.push({ stage: record.submitted ? 'observation' : 'pre_submission', message: error.message });
        if (record.submitted) { if (record.status === 'harness_error') record.status = 'timeout_unknown'; stop = true; }
      } finally {
        record.logErrors = await collector.all();
        record.logEndCursors = structuredClone(collector.cursors);
        try { await save(join(directory, 'after.json'), await adapter.snapshot()); }
        catch (error) { record.errors.push({ stage: 'snapshot_after', message: error.message }); }
        record.finishedAt = new Date().toISOString();
        record.elapsedMs = Math.round(performance.now() - caseStarted);
        await save(join(directory, 'result.json'), record);
        await append(join(output, 'results.jsonl'), record);
        for (const { id } of topology.nodes) {
          await mkdir(join(output, 'nodes', id), { recursive: true, mode: 0o700 });
          await append(join(output, 'nodes', id, 'transactions.jsonl'), { caseId: c.id, paymentHash: record.paymentHash ?? null,
            role: id === c.sender ? 'sender' : id === c.recipient ? 'recipient'
              : record.metrics?.paths.some(path => path.nodes.includes(id)) ? 'forwarder' : 'not_observed',
            status: record.status, start: record.logStartCursors[id], end: record.logEndCursors[id] });
        }
        rows.push(record);
        await event('case_finished', { caseId: c.id, status: record.status, totalFeeMsat: record.metrics?.totalFeeMsat ?? null,
          paths: record.metrics?.pathCount ?? null, errors: record.errors.length, logErrors: record.logErrors.length });
      }
    }
    manifest.status = rows.length === suite.cases.length ? 'completed' : 'incomplete';
  } catch (error) {
    manifest.status = 'harness_error'; manifest.error = error.message;
    await event('harness_error', { message: error.message });
    process.exitCode = 1;
  } finally {
    manifest.finishedAt = new Date().toISOString();
    manifest.recordedCases = rows.length;
    manifest.counts = Object.fromEntries([...new Set(rows.map(r => r.status))].map(s => [s, rows.filter(r => r.status === s).length]));
    manifest.completeMeasurements = rows.filter(r => r.metrics?.complete).length;
    manifest.finalLogErrors = await collector.all();
    await save(join(output, 'manifest.json'), manifest);
    await save(join(output, 'summary.json'), { ...manifest, initialState: undefined, initialHistory: undefined });
    if (manifest.status !== 'completed' || rows.some(r => ['harness_error', 'timeout_unknown', 'inconsistent', 'invalid_baseline'].includes(r.status)
      || r.logErrors.length || r.errors.some(e => e.stage === 'snapshot_after') || (r.status === 'succeeded' && !r.metrics?.complete)) || manifest.finalLogErrors.length) process.exitCode = 1;
    await event('finished', { status: manifest.status, output, counts: manifest.counts });
  }
}
