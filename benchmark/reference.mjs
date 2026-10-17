import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { readJson, hash, canonical, comparableState, check, save } from './common.mjs';

export async function verifyInputs() {
  const lock = await readJson(new URL('./inputs-lock.json', import.meta.url));
  for (const [name, expected] of Object.entries(lock.files)) {
    check(['scenarios.json', 'topology.json', 'common.mjs', 'compare.mjs'].includes(name), 'Invalid locked file');
    const actual = createHash('sha256').update(await readFile(new URL(name, import.meta.url))).digest('hex');
    check(actual === expected, `${name} differs from the AIR-GB input contract; synchronize inputs deliberately`);
  }
  return lock;
}

export function stateDifferences(expected, actual) {
  const differences = [];
  for (const node of [...new Set([...Object.keys(expected), ...Object.keys(actual)])].sort()) {
    if (canonical(expected[node]) !== canonical(actual[node])) differences.push({ node, expected: expected[node] ?? null, actual: actual[node] ?? null });
  }
  return differences;
}

export async function requireReference(directory, manifest, initial, config, output) {
  const reference = await readJson(join(directory, 'manifest.json'));
  check(reference.schemaVersion === 1 && reference.mode === 'ai', 'Reference must be an AIR-GB AI run');
  check(reference.suiteHash === manifest.suiteHash && reference.topologyHash === manifest.topologyHash, 'Different scenarios or topology from the AI run');
  check(canonical(reference.timeouts) === canonical(manifest.timeouts), 'Match the AI run timeout configuration');
  const original = await readJson(join(directory, 'initial-state.json'));
  const expected = comparableState(original, reference.assetAliases);
  check(hash(expected) === reference.initialStateHash, 'AI initial-state file disagrees with its manifest');
  const actual = comparableState(initial, config.assets);
  const differences = stateDifferences(expected, actual);
  await save(join(output, 'initial-state-comparison.json'), {
    referenceRunId: reference.runId, matches: differences.length === 0,
    expectedHash: reference.initialStateHash, observedHash: hash(actual), differences,
    notes: ['Forwarding fee policies are not exposed by the snapshot API and must be configured identically',
      'Distinct pubkeys, channel IDs and contract IDs are normalized to node and asset aliases'],
  });
  check(differences.length === 0, 'Initial state differs from AIR-GB; no payments submitted. See initial-state-comparison.json');
  return { runId: reference.runId, initialStateHash: reference.initialStateHash, label: reference.label };
}
