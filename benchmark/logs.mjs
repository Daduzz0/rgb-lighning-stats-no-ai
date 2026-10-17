import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, writeFile, chmod, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
const exec = promisify(execFile);
const ANSI = /\x1b\[[0-9;]*m/g;
const MARKER = 'RGB_STATS_EVENT ';
const integer = value => {
  if (value == null) return null;
  if (typeof value === 'number' && !Number.isSafeInteger(value)) throw new Error('Telemetry integer exceeds JSON precision');
  if (!/^\d+$/.test(String(value))) throw new Error('Invalid telemetry integer');
  return String(value);
};

export function parseEvents(text) {
  const events = [];
  for (const raw of text.split('\n')) {
    const line = raw.replace(ANSI, '');
    const offset = line.indexOf(MARKER);
    if (offset < 0) {
      const failure = /Failed to send payment to payment ID [0-9a-f]+, payment hash ([0-9a-f]{64}): ([A-Za-z0-9_]+)/.exec(line);
      if (failure) events.push({ kind: 'payment_failed', paymentHash: failure[1], error: failure[2], source: 'stock_ldk_events' });
      continue;
    }
    const event = JSON.parse(line.slice(offset + MARKER.length, line.lastIndexOf('}') + 1));
    const base = { paymentHash: event.paymentHash, timestampMs: Date.parse(line.split(' ')[0]), source: 'stock_ldk_events' };
    if (event.event === 'payment_sent') events.push({ ...base, kind: 'payment_succeeded', feeMsat: integer(event.feeMsat) });
    if (['path_succeeded', 'path_failed'].includes(event.event)) {
      if (!Array.isArray(event.hops)) throw new Error('Missing native hops');
      events.push({ ...base, kind: event.event, path: { amountMsat: integer(event.amountMsat), feeMsat: integer(event.feeMsat),
        assetAmount: integer(event.hops.at(-1)?.rgbAmount), blinded: event.blinded === true,
        hops: event.hops.map(h => ({ nodeId: h.pubkey, shortChannelId: integer(h.shortChannelId),
          feeOrFinalAmountMsat: integer(h.feeOrFinalAmountMsat), cltvExpiryDelta: h.cltvExpiryDelta,
          assetId: h.rgbAssetId ?? null, assetAmount: integer(h.rgbAmount) })),
      } });
    }
  }
  return events;
}

export class LogCollector {
  constructor({ topology, config, output }) {
    Object.assign(this, { topology, config, output });
    this.startedAt = new Date().toISOString(); this.cursors = {}; this.containers = {}; this.collectedEvents = {};
    this.composeFile = resolve(config.composeFile ?? new URL('./compose.yaml', import.meta.url).pathname);
    this.project = config.project ?? 'rgb-stats-benchmark';
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(this.project)) throw new Error('Invalid Compose project');
  }
  async container(node) {
    if (!this.topology.nodes.some(n => n.id === node)) throw new Error('Unknown node');
    if (!this.containers[node]) {
      const { stdout } = await exec('docker', ['compose', '-p', this.project, '-f', this.composeFile, 'ps', '-q', node], { timeout: 30000 });
      const id = stdout.trim();
      if (!/^[a-f0-9]{12,64}$/.test(id)) throw new Error(`No unique running container for ${node}`);
      this.containers[node] = id;
    }
    return this.containers[node];
  }
  async provenance(node) {
    const id = await this.container(node);
    const { stdout } = await exec('docker', ['inspect', '--format', '{{.Image}}', id], { timeout: 30000 });
    const imageId = stdout.trim();
    const image = await exec('docker', ['image', 'inspect', '--format', '{{json .Config.Labels}}', imageId], { timeout: 30000 });
    const binary = await exec('docker', ['exec', id, 'sha256sum', '/usr/local/bin/rgb-lightning-node'], { timeout: 30000 });
    return { containerId: id, imageId, binarySha256: binary.stdout.trim().split(/\s+/)[0], labels: JSON.parse(image.stdout) ?? {} };
  }
  async collect(node) {
    const id = await this.container(node);
    const response = await exec('docker', ['logs', '--timestamps', '--since', this.startedAt, id],
      { timeout: 30000, maxBuffer: 64 * 1024 * 1024 });
    const directory = join(this.output, 'nodes', node);
    await mkdir(directory, { recursive: true, mode: 0o700 });
    const cursors = this.cursors[node] ??= {};
    for (const [name, text] of [['stdout.log', response.stdout], ['stderr.log', response.stderr]]) {
      const offset = Buffer.byteLength(text);
      if (cursors[name] && cursors[name].offset > offset) throw new Error(`Container logs truncated: ${node}/${name}`);
      await writeFile(join(directory, name), text, { mode: 0o600 });
      cursors[name] = { offset, ino: id };
    }
    this.collectedEvents[node] = parseEvents(`${response.stdout}\n${response.stderr}`);
    await writeFile(join(directory, 'benchmark-events.jsonl'), this.collectedEvents[node].map(e => JSON.stringify(e)).join('\n') + '\n', { mode: 0o600 });
  }
  async events(node) { return this.collectedEvents[node] ?? []; }
  async all() {
    const errors = [];
    for (const { id } of this.topology.nodes) {
      try {
        await this.collect(id);
        // Copy only diagnostic logs, never credentials, keys or payment databases.
        for (const [source, destination] of [['/RLNdata/logs/.', 'daemon'], ['/RLNdata/.ldk/logs/.', 'ldk']]) {
          const folder = join(this.output, 'nodes', id, destination);
          await mkdir(folder, { recursive: true, mode: 0o700 });
          await exec('docker', ['cp', `${await this.container(id)}:${source}`, folder], { timeout: 30000 });
          for (const entry of await readdir(folder, { withFileTypes: true })) {
            if (entry.isFile()) await chmod(join(folder, entry.name), 0o600);
          }
        }
      } catch (error) { errors.push({ node: id, error: error.message }); }
    }
    return errors;
  }
}
