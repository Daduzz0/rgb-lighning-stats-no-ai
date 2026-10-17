import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir, appendFile } from 'node:fs/promises';
import { dirname } from 'node:path';

export const readJson = async path => JSON.parse(await readFile(path, 'utf8'));
export const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);
export const hash = value => createHash('sha256').update(canonical(value)).digest('hex');
export const save = async (path, data) => {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  await writeFile(path, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
};
export const append = async (path, data) => appendFile(path, JSON.stringify(data) + '\n', { mode: 0o600 });
export const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
export function check(condition, message) { if (!condition) throw new Error(message); }
export const normalizeStatus = status => {
  const s = String(status).toLowerCase();
  return ['succeeded', 'success', 'settled', 'complete'].includes(s) ? 'succeeded'
    : ['failed', 'failure'].includes(s) ? 'failed' : 'pending';
};
export function validateSuite(suite, topology, config) {
  check(suite.schemaVersion === 1 && suite.network === 'regtest', 'Unsupported suite');
  check(topology.network === 'regtest', 'Only regtest is supported');
  check(suite.cases.length === 20, 'The suite must contain exactly 20 cases');
  check(new Set(suite.cases.map(c => c.id)).size === 20, 'Duplicate case IDs');
  const nodes = new Set(topology.nodes.map(n => n.id));
  for (const c of suite.cases) {
    check(/^[a-z0-9-]+$/.test(c.id), 'Invalid case ID');
    check(nodes.has(c.sender) && nodes.has(c.recipient) && c.sender !== c.recipient, `Invalid endpoints: ${c.id}`);
    check(/^[1-9][0-9]*$/.test(c.amountMsat) && BigInt(c.amountMsat) <= BigInt(Number.MAX_SAFE_INTEGER), `Invalid amount: ${c.id}`);
    if (c.asset) {
      check(/^[1-9][0-9]*$/.test(c.assetAmount) && BigInt(c.assetAmount) <= BigInt(Number.MAX_SAFE_INTEGER), `Invalid asset amount: ${c.id}`);
      check(typeof config.assets?.[c.asset] === 'string' && /^rgb:/.test(config.assets[c.asset]) && !config.assets[c.asset].includes('REPLACE'), `Configure assets.${c.asset} with the real contract ID`);
    }
  }
  for (const key of ['paymentTimeoutMs', 'requestTimeoutMs', 'observationTimeoutMs']) {
    if (config[key] !== undefined) check(Number.isSafeInteger(config[key]) && config[key] > 0, `Invalid ${key}`);
  }
}

// Public keys, channel IDs and contract IDs differ between repositories. Keep
// directional liquidity and topology, mapping identities to stable aliases.
export function comparableState(snapshot, assets) {
  const aliases = new Map(Object.entries(snapshot).map(([id, row]) => [row.nodeId, id]));
  const assetNames = new Map(Object.entries(assets ?? {}).map(([name, id]) => [id, name]));
  return Object.fromEntries(Object.entries(snapshot).sort().map(([id, row]) => [id, {
    channels: row.channels.map(c => ({ peer: aliases.get(c.peerNodeId) ?? c.peerNodeId,
      asset: c.assetId ? assetNames.get(c.assetId) ?? c.assetId : null,
      capacitySat: c.capacitySat, outboundMsat: c.outboundMsat, inboundMsat: c.inboundMsat,
      assetLocalAmount: c.assetLocalAmount, assetRemoteAmount: c.assetRemoteAmount, isUsable: c.isUsable,
    })).sort((a, b) => canonical(a).localeCompare(canonical(b))),
  }]));
}

export function measurements(events, paymentHash, payment, aliases = {}) {
  const matched = events.filter(e => e.paymentHash === paymentHash);
  const sent = matched.filter(e => e.kind === 'payment_succeeded');
  const paths = matched.filter(e => e.kind === 'path_succeeded').map(e => ({ ...e.path,
    nodes: [payment.sender, ...e.path.hops.map(h => aliases[h.nodeId] ?? h.nodeId)],
  }));
  const fee = sent.at(-1)?.feeMsat ?? null;
  const decimal = v => typeof v === 'string' && /^(0|[1-9][0-9]*)$/.test(v);
  const validPaths = paths.length > 0 && paths.every(p => !p.blinded && p.hops?.length && decimal(p.amountMsat) && decimal(p.feeMsat)
    && p.nodes.at(-1) === payment.recipient && (!payment.asset || decimal(p.assetAmount)));
  const complete = validPaths && decimal(fee)
    && paths.reduce((n, p) => n + BigInt(p.amountMsat), 0n) === BigInt(payment.amountMsat)
    && paths.reduce((n, p) => n + BigInt(p.feeMsat), 0n) === BigInt(fee)
    && (!payment.asset || paths.reduce((n, p) => n + BigInt(p.assetAmount), 0n) === BigInt(payment.assetAmount));
  return { totalFeeMsat: decimal(fee) ? fee : null, paths, pathCount: validPaths ? paths.length : null,
    totalHops: validPaths ? paths.reduce((n, p) => n + p.hops.length, 0) : null,
    maxHops: validPaths ? Math.max(...paths.map(p => p.hops.length)) : null,
    actualMpp: validPaths ? paths.length > 1 : null,
    failedPathAttempts: matched.filter(e => e.kind === 'path_failed').length,
    complete, source: sent.at(-1)?.source ?? 'settled_ldk_events', missing: complete ? [] : ['settled_fee_or_complete_paths'],
  };
}

// Conservative stock-node fallback: one settled flow, both ends of every
// changed channel agreeing, and the sender's debit matching PaymentSent's fee.
// Requires exclusive use of these nodes during the run. Ambiguous or branching
// flows remain unmeasured; channel deltas cannot reveal per-HTLC shard counts.
export function singlePathFromBalances(before, after, payment, paidFeeMsat) {
  try {
    if (typeof paidFeeMsat !== 'string' || !/^\d+$/.test(paidFeeMsat)) return null;
    const aliases = new Map(Object.entries(before).map(([id, row]) => [row.nodeId, id]));
    const edges = [];
    for (const [from, state] of Object.entries(before)) {
      for (const channel of state.channels) {
        const end = after[from]?.channels.find(c => c.channelId === channel.channelId);
        if (!end || channel.outboundMsat == null || end.outboundMsat == null) return null;
        const debit = BigInt(channel.outboundMsat) - BigInt(end.outboundMsat);
        if (debit <= 0n) continue;
        const to = aliases.get(channel.peerNodeId);
        if (!to) return null;
        const reverse = before[to].channels.find(c => c.channelId === channel.channelId);
        const reverseEnd = after[to]?.channels.find(c => c.channelId === channel.channelId);
        if (reverse?.outboundMsat == null || reverseEnd?.outboundMsat == null
          || BigInt(reverseEnd.outboundMsat) - BigInt(reverse.outboundMsat) !== debit) return null;
        if (payment.asset) {
          if ([channel.assetLocalAmount, end.assetLocalAmount, reverse.assetLocalAmount, reverseEnd.assetLocalAmount].some(v => v == null)) return null;
          if (BigInt(channel.assetLocalAmount) - BigInt(end.assetLocalAmount) !== BigInt(payment.assetAmount)
            || BigInt(reverseEnd.assetLocalAmount) - BigInt(reverse.assetLocalAmount) !== BigInt(payment.assetAmount)) return null;
        }
        edges.push({ from, to, channelId: channel.channelId, amount: debit, nodeId: channel.peerNodeId });
      }
    }
    const path = [], visited = new Set([payment.sender]);
    let at = payment.sender;
    while (at !== payment.recipient) {
      const next = edges.filter(e => e.from === at);
      if (next.length !== 1 || visited.has(next[0].to)) return null;
      path.push(next[0]); at = next[0].to; visited.add(at);
    }
    if (!path.length || path.length !== edges.length || path.at(-1).amount !== BigInt(payment.amountMsat)
      || path[0].amount !== BigInt(payment.amountMsat) + BigInt(paidFeeMsat)) return null;
    if (path.some((edge, i) => i + 1 < path.length && edge.amount < path[i + 1].amount)) return null;
    return { totalFeeMsat: paidFeeMsat, paths: [{ amountMsat: payment.amountMsat, assetAmount: payment.assetAmount,
      feeMsat: paidFeeMsat, nodes: [payment.sender, ...path.map(e => e.to)],
      hops: path.map((edge, i) => ({ nodeId: edge.nodeId, channelId: edge.channelId, shortChannelId: null,
        feeOrFinalAmountMsat: String(i + 1 < path.length ? edge.amount - path[i + 1].amount : edge.amount) })),
    }], pathCount: 1, totalHops: path.length, maxHops: path.length,
    actualMpp: null, failedPathAttempts: null, complete: true, missing: [],
    source: 'settled_channel_balance_deltas_and_payment_sent_log',
    limitations: ['Requires no concurrent traffic', 'Shard count and failed attempts are not observable from net balances'],
    };
  } catch { return null; }
}
