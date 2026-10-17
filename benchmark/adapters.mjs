import { check, normalizeStatus, readJson, measurements as sharedMeasurements } from './common.mjs';

export class RestError extends Error {
  constructor(node, endpoint, status, name) {
    super(`${node} ${endpoint}: HTTP ${status} (${name})`);
    Object.assign(this, { status, code: name });
  }
}

export function createStockAdapter({ topology, config, collector }) {
  const identities = new Map();
  const expectedNodes = topology.nodes.map(n => n.id).sort();
  check(JSON.stringify(Object.keys(config.nodes ?? {}).sort()) === JSON.stringify(expectedNodes), 'Configure exactly the 20 shared nodes');
  check(config.network === 'regtest', 'Only regtest is supported');
  check(config.baseline?.mpp === false && config.baseline?.routing === 'stock', 'Expected stock routing and disabled MPP');
  const decimal = n => n === undefined || n === null ? null :
    typeof n === 'number' && !Number.isSafeInteger(n) ? (() => { throw new Error('Unsafe integer in daemon response'); })() : String(n);
  const request = async (node, endpoint, body) => {
    const entry = config.nodes[node];
    const base = new URL(entry.url);
    check(base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)
      && !base.username && !base.password && !base.search && !base.hash && base.pathname === '/', 'Use a local daemon root URL');
    const headers = { 'content-type': 'application/json' };
    if (entry.bearerTokenEnv) {
      check(process.env[entry.bearerTokenEnv], `Missing bearer token variable ${entry.bearerTokenEnv}`);
      headers.authorization = `Bearer ${process.env[entry.bearerTokenEnv]}`;
    }
    const response = await fetch(new URL(endpoint, base), {
      method: body === undefined ? 'GET' : 'POST', headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(config.requestTimeoutMs ?? 120000), redirect: 'error',
    });
    const data = await response.json();
    if (!response.ok) throw new RestError(node, endpoint, response.status, typeof data.name === 'string' ? data.name : 'HTTP_ERROR');
    check(data && typeof data === 'object' && !Array.isArray(data), 'Malformed daemon response');
    return data;
  };
  return {
    name: 'rgb-tools-upstream-single-path',
    async metadata() {
      const pinned = await readJson(new URL('../docker/upstream-lock.json', import.meta.url));
      check(config.baseline.repositoryCommit === pinned.commit, 'Configured baseline revision differs from the pinned daemon');
      const containers = {};
      for (const { id } of topology.nodes) {
        const deployment = await collector.provenance(id);
        check(deployment.labels['org.opencontainers.image.revision'] === pinned.commit, `Wrong upstream revision on ${id}`);
        check(deployment.labels['rgb.stats.bolt11-max-paths'] === '1'
          && deployment.labels['rgb.stats.routing'] === 'upstream-single-path', `Use the benchmark single-path image for ${id}`);
        check(deployment.labels['rgb.stats.instrumentation'] === 'payment-events-v1', `Missing payment observations on ${id}`);
        containers[id] = deployment;
      }
      return { mode: 'baseline', backend: pinned.commit, mpp: false, routing: 'stock', maxPaths: 1,
        enforcement: 'RouteParametersConfig::default().with_max_path_count(1), BOLT11 only',
        upstream: pinned, containers };
    },
    async snapshot() {
      const result = {}, seen = new Set();
      for (const { id } of topology.nodes) {
        const network = await request(id, 'networkinfo');
        const info = await request(id, 'nodeinfo');
        check(network.network?.toLowerCase() === 'regtest', `${id} is not on regtest`);
        check(/^0[23][0-9a-f]{64}$/i.test(info.pubkey) && !seen.has(info.pubkey), `Invalid or duplicate identity: ${id}`);
        check(!config.nodes[id].expectedPubkey || config.nodes[id].expectedPubkey === info.pubkey, `Identity changed: ${id}`);
        seen.add(info.pubkey); identities.set(id, info.pubkey);
        const response = await request(id, 'listchannels');
        check(Array.isArray(response.channels), `Missing channels for ${id}`);
        result[id] = { nodeId: info.pubkey, channels: response.channels.map(c => ({
          channelId: c.channel_id, peerNodeId: c.peer_pubkey, shortChannelId: decimal(c.short_channel_id),
          capacitySat: decimal(c.capacity_sat), outboundMsat: decimal(c.outbound_balance_msat), inboundMsat: decimal(c.inbound_balance_msat),
          assetId: c.asset_id ?? null, assetLocalAmount: decimal(c.asset_local_amount), assetRemoteAmount: decimal(c.asset_remote_amount),
          isUsable: c.is_usable, public: c.public, fundingTxid: c.funding_txid ?? null,
        })) };
      }
      return result;
    },
    async invoice(c) {
      return (await request(c.recipient, 'lninvoice', { amt_msat: Number(c.amountMsat), expiry_sec: 3600,
        ...(c.asset ? { asset_id: config.assets[c.asset], asset_amount: Number(c.assetAmount) } : {}) })).invoice;
    },
    async select() {
      return { selectionSource: 'stock_single_path', model: null, modelDecision: null,
        recommendedRouting: null, configuredMaxPaths: 1,
        rationale: 'Upstream router and scorer; BOLT11 max_path_count is fixed to one by the benchmark build' };
    },
    async prepare(c, invoice, _decision, id) {
      const decoded = await request(c.sender, 'decodelninvoice', { invoice });
      check(decoded.network?.toLowerCase() === 'regtest' && /^[0-9a-f]{64}$/i.test(decoded.payment_hash), 'Invalid regtest invoice');
      check(decoded.payee_pubkey === identities.get(c.recipient), 'Invoice payee mismatch');
      check(decimal(decoded.amt_msat) === c.amountMsat && (decoded.asset_id ?? null) === (c.asset ? config.assets[c.asset] : null), 'Invoice amount or asset mismatch');
      if (c.asset) check(decimal(decoded.asset_amount) === c.assetAmount, 'RGB amount mismatch');
      check((decoded.timestamp + decoded.expiry_sec) * 1000 > Date.now(), 'Invoice expired');
      return { id, invoice, paymentHash: decoded.payment_hash.toLowerCase() };
    },
    async send(c, handle) {
      // Exactly one send call; never pass fork-specific algorithms, weights or paths.
      const response = await request(c.sender, 'sendpayment', { invoice: handle.invoice });
      check(response.payment_hash === handle.paymentHash, 'Unexpected hash in send response');
      return response;
    },
    async status(c, handle) {
      const { payment } = await request(c.sender, 'getpayment', { payment_hash: handle.paymentHash });
      check(payment?.payment_hash === handle.paymentHash && payment.inbound === false, 'Unexpected outbound payment');
      check(decimal(payment.amt_msat) === c.amountMsat && (payment.asset_id ?? null) === (c.asset ? config.assets[c.asset] : null), 'Payment amount or asset mismatch');
      if (c.asset) check(decimal(payment.asset_amount) === c.assetAmount, 'Payment RGB amount mismatch');
      const received = await request(c.recipient, 'invoicestatus', { invoice: handle.invoice });
      check(['pending', 'succeeded', 'failed'].includes(String(payment.status).toLowerCase()), 'Unknown sender status');
      check(['pending', 'succeeded', 'failed', 'expired'].includes(String(received.status).toLowerCase()), 'Unknown recipient status');
      return { sender: normalizeStatus(payment.status), receiver: normalizeStatus(received.status), paymentHash: handle.paymentHash };
    },
  };
}

// Some upstream BTC payments exceed the invoice to meet a channel's HTLC minimum.
// Preserve the real amounts and require PaymentSent's fee to account for the excess.
export function measurements(events, paymentHash, payment, aliases) {
  const result = sharedMeasurements(events, paymentHash, payment, aliases);
  const paths = result.paths;
  const numeric = v => typeof v === 'string' && /^\d+$/.test(v);
  if (!paths.length || !paths.every(p => numeric(p.amountMsat) && numeric(p.feeMsat)) || !numeric(result.totalFeeMsat)) return result;
  const delivered = paths.reduce((n, p) => n + BigInt(p.amountMsat), 0n);
  const excess = delivered - BigInt(payment.amountMsat);
  result.deliveredAmountMsat = String(delivered);
  result.overpaymentMsat = excess >= 0n ? String(excess) : null;
  if (excess > 0n && paths.every(p => !p.blinded && p.hops.length && p.nodes.at(-1) === payment.recipient)
    && paths.reduce((n, p) => n + BigInt(p.feeMsat), 0n) + excess === BigInt(result.totalFeeMsat)
    && (!payment.asset || paths.every(p => numeric(p.assetAmount))
      && paths.reduce((n, p) => n + BigInt(p.assetAmount), 0n) === BigInt(payment.assetAmount))) {
    result.complete = true; result.missing = [];
  }
  return result;
}
