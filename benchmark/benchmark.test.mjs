// Pure contract checks, intentionally separate from the live benchmark.
import test from 'node:test';
import assert from 'node:assert/strict';
import { measurements, singlePathFromBalances, hash, comparableState } from './common.mjs';
import { compareRuns } from './compare.mjs';
const payment = { sender: 'alice', recipient: 'bob', amountMsat: '10000', asset: null, assetAmount: null };

test('missing settled fee is unknown, never zero or the planned fee', () => {
  const result = measurements([{ paymentHash: 'x', kind: 'route_found', feeMsat: '5' }], 'x', payment);
  assert.equal(result.totalFeeMsat, null);
  assert.equal(result.complete, false);
});

test('MPP aggregates all settled parts and ignores other payment hashes', () => {
  const events = [{ kind: 'payment_succeeded', paymentHash: 'x', feeMsat: '5' },
    ...[['6000', '2'], ['4000', '3']].map(([amountMsat, feeMsat]) => ({ kind: 'path_succeeded', paymentHash: 'x',
      path: { amountMsat, feeMsat, blinded: false, hops: [{ nodeId: 'pub-bob' }] } })),
    { kind: 'path_succeeded', paymentHash: 'other', path: {} }];
  const result = measurements(events, 'x', payment, { 'pub-bob': 'bob' });
  assert.equal(result.complete, true);
  assert.equal(result.actualMpp, true);
  assert.equal(result.totalHops, 2);
  assert.equal(measurements(events.slice(0, 2), 'x', payment, { 'pub-bob': 'bob' }).complete, false);
});

test('stock reconstruction requires matching debits, credits and paid fee', () => {
  const before = { alice: { nodeId: 'a', channels: [{ channelId: 'ch', peerNodeId: 'b', outboundMsat: '20000' }] },
    bob: { nodeId: 'b', channels: [{ channelId: 'ch', peerNodeId: 'a', outboundMsat: '0' }] } };
  const after = structuredClone(before);
  after.alice.channels[0].outboundMsat = '10000'; after.bob.channels[0].outboundMsat = '10000';
  assert.equal(singlePathFromBalances(before, after, payment, '0').complete, true);
  assert.equal(singlePathFromBalances(before, after, payment, '1'), null);
  after.bob.channels[0].outboundMsat = '9999';
  assert.equal(singlePathFromBalances(before, after, payment, '0'), null);
});

test('comparison ignores runtime identities but preserves directional liquidity', () => {
  const state = (a, b, contract) => ({ alice: { nodeId: a, channels: [{ peerNodeId: b, assetId: contract, outboundMsat: '10' }] }, bob: { nodeId: b, channels: [] } });
  assert.equal(hash(comparableState(state('a', 'b', 'rgb:x'), { TEST: 'rgb:x' })),
    hash(comparableState(state('c', 'd', 'rgb:y'), { TEST: 'rgb:y' })));
});

test('mismatched starting state is rejected and costs use matched successes only', () => {
  const manifest = { schemaVersion: 1, status: 'completed', suiteHash: 'suite', topologyHash: 'topology', initialStateHash: 'state', execution: 'sequential-live', label: 'same', timeouts: {} };
  const rows = Array.from({ length: 20 }, (_, i) => ({ caseId: String(i), caseHash: String(i), status: 'succeeded',
    metrics: { totalFeeMsat: '10', complete: true, totalHops: 2 }, logErrors: [] }));
  const baseline = structuredClone(rows); baseline[0].status = 'failed';
  const report = compareRuns({ ...manifest, mode: 'ai' }, { ...manifest, mode: 'baseline' }, rows, baseline);
  assert.equal(report.comparable, true);
  assert.equal(report.fees.sampleCount, 19);
  assert.equal(report.successRateDeltaPercentagePoints, 5);
  assert.equal(compareRuns({ ...manifest, mode: 'ai' }, { ...manifest, mode: 'baseline', initialStateHash: 'different' }, rows, baseline).comparable, false);
});
