import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { parseEvents } from './logs.mjs';
import { createStockAdapter, measurements } from './adapters.mjs';
import { verifyInputs, requireReference } from './reference.mjs';
import { readJson, save, hash, comparableState } from './common.mjs';

const paymentHash = 'a'.repeat(64);
const payment = { sender: 'alice', recipient: 'bob', amountMsat: '10000', asset: null };
const line = event => `2026-10-17T10:00:00.000Z INFO RGB_STATS_EVENT ${JSON.stringify({ paymentHash, ...event })}`;
const path = (amountMsat = 10000, feeMsat = 5) => ({ event: 'path_succeeded', amountMsat, feeMsat, blinded: false,
  hops: [{ pubkey: 'pub-bob', shortChannelId: '9007199254740993', feeOrFinalAmountMsat: amountMsat,
    cltvExpiryDelta: 40, rgbAmount: null, rgbAssetId: null }] });
const observed = (amount = 10000, fee = 5) => parseEvents([
  line(path(amount)), line({ event: 'payment_sent', feeMsat: fee }),
].join('\n'));

test('native events preserve actual fees, nodes and string channel IDs', () => {
  const result = measurements(observed(), paymentHash, payment, { 'pub-bob': 'bob' });
  assert.equal(result.complete, true);
  assert.equal(result.totalFeeMsat, '5');
  assert.equal(result.actualMpp, false);
  assert.equal(result.totalHops, 1);
  assert.deepEqual(result.paths[0].nodes, ['alice', 'bob']);
  assert.equal(result.paths[0].hops[0].shortChannelId, '9007199254740993');
  assert.equal(measurements(observed(), 'other', payment).totalFeeMsat, null);
});

test('HTLC excess is measured only when total fee reconciles', () => {
  const result = measurements(observed(11000, 1005), paymentHash, payment, { 'pub-bob': 'bob' });
  assert.equal(result.complete, true);
  assert.equal(result.deliveredAmountMsat, '11000');
  assert.equal(result.overpaymentMsat, '1000');
  assert.equal(measurements(observed(11000, 5), paymentHash, payment, { 'pub-bob': 'bob' }).complete, false);
  assert.equal(measurements(observed(9000, 5), paymentHash, payment, { 'pub-bob': 'bob' }).complete, false);
  assert.equal(measurements(observed(11000, 1005), paymentHash,
    { ...payment, asset: 'TEST', assetAmount: '10' }, { 'pub-bob': 'bob' }).complete, false);
});

test('failed path attempts remain separate from settled parts', () => {
  const events = [...observed(), ...parseEvents(line({ ...path(), event: 'path_failed' }))];
  const result = measurements(events, paymentHash, payment, { 'pub-bob': 'bob' });
  assert.equal(result.failedPathAttempts, 1);
  assert.equal(result.pathCount, 1);
  assert.equal(result.actualMpp, false);
  assert.throws(() => parseEvents(line({ event: 'payment_sent', feeMsat: Number.MAX_SAFE_INTEGER + 1 })), /precision/);
});

test('send body contains only the invoice and an ambiguous error is never retried', async t => {
  const topology = { nodes: [{ id: 'alice' }, { id: 'bob' }] };
  const config = { network: 'regtest', baseline: { mpp: false, routing: 'stock' },
    nodes: { alice: { url: 'http://127.0.0.1:8801' }, bob: { url: 'http://127.0.0.1:8802' } } };
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url: String(url), options });
    throw new Error('connection lost after request');
  });
  const adapter = createStockAdapter({ topology, config });
  await assert.rejects(adapter.send(payment, { invoice: 'lnbcrt-example', paymentHash }), /connection lost/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:8801/sendpayment');
  assert.deepEqual(JSON.parse(calls[0].options.body), { invoice: 'lnbcrt-example' });
});

test('shared inputs are locked and retain twenty nodes and peripheral cases', async () => {
  await verifyInputs();
  const topology = await readJson(new URL('./topology.json', import.meta.url));
  const suite = await readJson(new URL('./scenarios.json', import.meta.url));
  assert.equal(topology.nodes.length, 20);
  assert.equal(suite.cases.length, 20);
  assert.equal(topology.channels.length, 15);
  for (const node of ['karl', 'tara', 'leo', 'saul']) {
    assert.ok(topology.nodes.some(n => n.id === node));
    assert.ok(!topology.channels.some(c => c.from === node || c.to === node));
  }
});

test('reference accepts remapped identities but rejects different starting liquidity', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'rgb-baseline-contract-'));
  try {
    const state = (a, b, amount) => ({ alice: { nodeId: a, channels: [{ peerNodeId: b, outboundMsat: amount }] },
      bob: { nodeId: b, channels: [] } });
    const original = state('ai-alice', 'ai-bob', '10');
    const manifest = { schemaVersion: 1, mode: 'ai', runId: 'ref', suiteHash: 'suite', topologyHash: 'topology',
      timeouts: {}, assetAliases: {}, initialStateHash: hash(comparableState(original, {})) };
    await save(join(directory, 'manifest.json'), manifest);
    await save(join(directory, 'initial-state.json'), original);
    const output = join(directory, 'output');
    await requireReference(directory, manifest, state('stock-alice', 'stock-bob', '10'), { assets: {} }, output);
    await assert.rejects(requireReference(directory, manifest, state('stock-alice', 'stock-bob', '9'), { assets: {} }, output), /Initial state differs/);
    const diff = await readJson(join(output, 'initial-state-comparison.json'));
    assert.equal(diff.matches, false);
    assert.equal(diff.differences[0].node, 'alice');
  } finally { await rm(directory, { recursive: true, force: true }); }
});
