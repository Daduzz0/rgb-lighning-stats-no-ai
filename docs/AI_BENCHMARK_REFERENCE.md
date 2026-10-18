# AI-Friendly Technical Specification: RGB & Bitcoin Lightning Routing Benchmark (`rgb-lighning-stats-no-ai` vs. `air-gb-lightning`)

> **Target Audience**: AI Coding Agents, Automated Benchmark Runners, and Protocol Engineers.
> **Purpose**: Exhaustive, file-level, and byte-accurate specification of the cross-repository benchmark suite, protocol bug fixes, topology engineering, AI guardrails, and experimental results comparing standard single-path `rgb-lightning-node` against the QVAC AI + Multi-Path Payment (MPP) `air-gb-lightning` stack.

---

## 1. Repository & Artifact Map

### 1.1. Primary Repositories
| Role | Absolute Workspace Path | Remote URL | Core Routing Stack |
| :--- | :--- | :--- | :--- |
| **Baseline Stack (`rgb-lighning-stats-no-ai`)** | `/home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai` | `https://github.com/Daduzz0/rgb-lighning-stats-no-ai.git` | Upstream `RGB-Tools/rgb-lightning-node` (`d4762ac3d1b3ca83f2f7b4f894e8569109dc572a`) patched with `RouteParametersConfig::default().with_max_path_count(1)` + `fix_rgb_htlc_accounting`. |
| **AI + MPP Stack (`air-gb-lightning`)** | `/home/pesce/GitLab_Projects/air-gb-lightning` | `https://github.com/ObvAstral/air-gb-lightning.git` | Custom forks `ObvAstral/rgb-lightning-node` (`7106edd0fa3dd0a8bc0f35e54cefac844495a068`, `feat/rgb-mpp`) + `ObvAstral/rust-lightning` (`2f19847fc65a16294abcc26188ba8443804662be`, `feat/rgb-mpp`) + QVAC Local LLM (`Qwen3.5-0.8B`) + TurboVec RAG. |

### 1.2. Key Modified & Created Files Across Both Repositories

#### In `/home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai` (Baseline Repository)
- `benchmark/topology.json` — Original 20-node, 15-channel declarative regtest fixture (`push_msat: 0`).
- `benchmark/topology-mpp.json` — Second 20-node, 15-channel declarative regtest fixture (`push_msat: 7000000`, low-fee main corridors, reverse-guarded cross-links, `heidi -> frank` privacy hop).
- `benchmark/scenarios.json` — 20 sequential live payment scenarios (`01-btc-direct` through `20-rgb-peripheral`).
- `benchmark/setup.py` — Regtest Docker orchestrator (`up`, `down`, `logs`) that initializes `bitcoind`, `esplora`, `proxy`, and 20 `rgb-lightning-node` containers (`alice`..`tara`), funds wallets, issues the `TEST` (`ROUTE`) RGB asset (`supply: 10000`), distributes initial allocations, opens 15 colored channels with `push_msat`, and waits for gossip convergence.
- `benchmark/run.mjs` — Baseline benchmark runner that verifies `assertPaymentReady` (peer connectivity + gossip convergence across all sender nodes), snapshots pre/post channel balances, creates invoices with scenario `amountMsat`, executes `POST /sendpayment`, collects LDK logs, and writes `manifest.json`, `cases.jsonl`, and `summary.json`.
- `benchmark/compare.mjs` — Deterministic cross-run comparator that verifies `initialStateHash` equality (`comparable: true`), computes paired fee/hop/settlement deltas, and writes `comparison.json` + `comparison.md`.
- `docker/single_path.py` — Build-time patch script applied inside `docker/Dockerfile.benchmark`:
  1. Forces single-path routing (`RouteParametersConfig::default().with_max_path_count(1)` in `src/routes.rs`).
  2. Applies `fix_rgb_htlc_accounting(rln_root)` to `rust-lightning/lightning/src/rgb_utils/mod.rs` and `rust-lightning/lightning/src/ln/channel.rs`.
- `BENCHMARK_RESULTS.md` — Executive summary and 20-case comparison tables for Run 1 (`topology.json`) and Run 2 (`topology-mpp.json`).

#### In `/home/pesce/GitLab_Projects/air-gb-lightning` (AI + MPP Repository)
- `topology.json` & `topology-mpp.json` — Identical declarative topology definitions used by the AI stack.
- `wallet/infra/benchmark/bootstrap-topology.mjs` — Automated end-to-end bootstrapper for the 20-node AIR-GB Docker Compose topology (`create`, `unlock`, BTC funding, RGB `issue`, RGB witness transfers, `openchannel` with `push_msat`, and `waitForGraphConvergence` via `/benchmark/gossip-status`).
- `wallet/infra/benchmark/compose.observations.yml` — Docker Compose overlay setting `AIRGB_BENCHMARK_EVENTS: "1"`, `QVAC_BRIDGE_HOST: "${QVAC_BRIDGE_HOST:-host.docker.internal}"`, and `QVAC_MODEL: "${QVAC_MODEL:-qwen-fast}"` across all 20 node containers.
- `wallet/infra/benchmark/run.mjs` — AI benchmark runner executing the 20 scenarios through AIR-GB's `/wallet/pay` endpoint (`selectionMode: 'benchmark_strict'`), enforcing `assertPaymentReady` before Case 01, and recording QVAC proposals, guardrail decisions, and LDK `.ldk/benchmark-events.jsonl` telemetry.
- `wallet/apps/local-service/channel-scoring.ts` — QVAC RAG store (`QvacRagStore` with SQLite + TurboVec `/v1/vector_stores`) and LLM channel classifier (`scoreChannelsWithQvac`) with deterministic physical liquidity override protection.
- `wallet/apps/local-service/routing-selector.ts` — `EphemeralRoutingContext` and `selectRoutingAlgorithmWithQvac` with deterministic guardrails (`max_paths` clamping, privacy single-path enforcement, and 0-RGB channel filtering).
- `wallet/apps/local-service/wallet.ts` & `wallet/apps/local-service/server.ts` — Exposes `push_msat` in `openChannel` and adds `GET /benchmark/gossip-status` to inspect LDK's internal `network_graph` channel/directional announcement counts.
- `wallet/infra/native/build.mjs` — Applies `fixRgbHtlcAccounting` during Rust compilation of `ObvAstral/rust-lightning`.

---

## 2. Deep Protocol Bug Discoveries & Exact Code Fixes

During live regtest execution of sequential RGB multi-hop and multi-path payments, five critical protocol, accounting, and orchestration bugs were uncovered and fixed. Any AI agent modifying either repository MUST preserve these invariants.

### 2.1. Bug 1: Rust LDK Multi-HTLC RGB Commitment Coloring Corruption (`color_commitment`)

#### Root Cause
In upstream `rgb-lightning-node` / `rust-lightning` (`rust-lightning/lightning/src/rgb_utils/mod.rs` and `rust-lightning/lightning/src/ln/channel.rs`):
1. When building a commitment transaction with multiple in-flight or settling RGB HTLCs (`color_commitment`), the original code iterated over included HTLCs, wrote `rgb_payment_info` files to disk, and computed the remaining channel RGB balances (`local_amt`, `remote_amt`) by subtracting `rgb_offered_htlc` / `rgb_received_htlc` from `last_rgb_payment_info.local_rgb_amount` / `remote_rgb_amount`.
2. Because `rgb_payment_info` files were written when an HTLC was *first* added to a commitment transaction, `last_rgb_payment_info` held a **stale snapshot** of `local_rgb_amount` and `remote_rgb_amount` from before earlier HTLCs settled or before concurrent MPP HTLCs were added!
3. Furthermore, `update_rgb_channel_amount_pending` was being called prematurely when promoting HTLCs from `LocalAnnounced` to `Committed` or `AwaitingRemoteRevokeToAnnounce` to `AwaitingAnnouncedRemoteRevoke`, **before** the HTLC was actually fulfilled with a preimage (`InboundHTLCRemovalReason::Fulfill`). If an HTLC failed downstream (e.g., during an MPP probe or over-capacity check), the channel's on-disk RGB balance was permanently corrupted, causing a subsequent panic (`underflow` or `Insufficient RGB`) and force-closing the channel.
4. Inbound/outbound RGB liquidity checks (`send_htlc` and `handle_update_add_htlc`) checked raw `get_local_rgb_amount()` / `get_remote_rgb_amount()` without subtracting already-pending outbound/inbound HTLCs (`pending_outbound_htlcs`, `holding_cell_htlc_updates`, `pending_inbound_htlcs`).

#### Exact Fix (`docker/single_path.py` in Baseline & `wallet/infra/native/build.mjs` in AIR-GB)
1. **Added `available_outbound_rgb(&self)` and `available_inbound_rgb(&self)` on `ChannelContext`**:
   - `available_outbound_rgb()` sums `rgb_payment` across `pending_outbound_htlcs` and `holding_cell_htlc_updates` (`HTLCUpdateAwaitingACK::AddHTLC`) and returns `info.local_rgb_amount.saturating_sub(offered)`.
   - `available_inbound_rgb()` sums `rgb_payment` across `pending_inbound_htlcs` and returns `info.remote_rgb_amount.saturating_sub(received)`.
2. **Computed exact settled RGB delta inside `build_commitment_transaction`**:
   - Tracks `value_to_self_claimed_rgb` (inbound HTLCs with a known preimage) and `value_to_remote_claimed_rgb` (outbound HTLCs with a known preimage).
   - Computes `delta = value_to_self_claimed_rgb as i128 - value_to_remote_claimed_rgb as i128` relative to `get_rgb_channel_info_pending(&self.channel_id, &self.ldk_data_dir)` and stores `rgb_balances: (u64, u64)` directly in `CommitmentData`.
3. **Passed `rgb_balances: (u64, u64)` into `color_commitment`**:
   - `color_commitment` now computes `local_amt = rgb_balances.0.checked_sub(rgb_offered_htlc)` and `remote_amt = rgb_balances.1.checked_sub(rgb_received_htlc)` instead of reading stale `last_rgb_payment_info` balances.
4. **Updated pending RGB balances ONLY on HTLC Fulfill**:
   - Removed premature `rgb_offered_htlc` / `rgb_received_htlc` mutations during `LocalAnnounced -> Committed` and `AwaitingRemoteRevokeToAnnounce -> AwaitingAnnouncedRemoteRevoke`.
   - Added `rgb_received_htlc += htlc.rgb_payment.map_or(0, |(_, amount)| amount)` strictly inside `InboundHTLCRemovalReason::Fulfill` and `rgb_offered_htlc += ...` strictly when outbound HTLCs fulfill.

---

### 2.2. Bug 2: LDK 1% BTC Channel Reserve Starving Return Flows After MPP

#### Root Cause
In Lightning Development Kit (LDK), every channel enforces a mandatory **1% counterparty channel reserve**:
$$\text{Reserve}_{\text{msat}} = 0.01 \times \text{Capacity}_{\text{sat}} \times 1000$$
For a `1,000,000` sat (`1,000,000,000` msat) channel opened with `push_msat = 0`:
- The funder (`from`) starts with `1,000,000,000` msat local BTC and `990,000,000` msat spendable outbound capacity.
- The counterparty (`to`, e.g., `judy`) starts with `0` msat local BTC and must accumulate **more than `10,000,000` msat (`10,000` sat)** on that specific channel before LDK allows `judy` to send a single satoshi outbound on that channel.
- In Scenario `02-rgb-mpp-100` (`alice -> judy`, `100` RGB, `30,000,000` msat total BTC carrier), `min_cost_flow` splits the payment proportionally to the RGB amounts (`60` RGB on `heidi-judy` and `40` RGB on `ivan-judy`):
  - `heidi-judy` receives `18,000,000` msat $\rightarrow$ `judy`'s spendable outbound BTC above the `10,000,000` msat reserve is only **`8,000,000` msat**.
  - `ivan-judy` receives `12,000,000` msat $\rightarrow$ `judy`'s spendable outbound BTC above the `10,000,000` msat reserve is only **`2,000,000` msat**.
- Every RGB HTLC in RLN requires a minimum carrier amount of **`3,000,000` msat (`3,000` sat)** (`MIN_RGB_HTLC_MSAT = 3_000_000`). And for `03-rgb-return-100` (`judy -> alice`, `100` RGB, `10,000,000` msat total carrier), the `40` RGB shard on `judy -> ivan` requires `4,000,000` msat + `3,000` msat routing fee = `4,003,000` msat. Because `2,000,000 < 4,003,000` msat, `judy` could never return the `40` RGB shard on `ivan-judy` when `push_msat = 0`!

#### Exact Fix (`topology-mpp.json`, `benchmark/setup.py`, `wallet.ts`, `bootstrap-topology.mjs`)
- Set `channelDefaults.push_msat: 7000000` (`7,000,000` msat = `7,000` sat pushed from `from` to `to` at channel open) in `topology-mpp.json`.
- Wired `push_msat` into `POST /openchannel` in both `rgb-lighning-stats-no-ai/benchmark/setup.py` and `air-gb-lightning/wallet/apps/local-service/wallet.ts` + `bootstrap-topology.mjs`.
- With `push_msat = 7,000,000`:
  - After `02-rgb-mpp-100`, `judy`'s local BTC balance on `heidi-judy` is `7,000,000 + 18,000,000 = 25,000,000` msat (**`15,000,000` msat spendable above the `10M` reserve**).
  - `judy`'s local BTC balance on `ivan-judy` is `7,000,000 + 12,000,000 = 19,000,000` msat (**`9,000,000` msat spendable above the `10M` reserve**).
  - Scenario `03-rgb-return-100` (`judy -> alice`, `60 + 40` RGB, `6,000,000 + 4,000,000` msat + `6,000` msat fees) now succeeds cleanly via 2-path MPP.

---

### 2.3. Bug 3: Asymmetric Reverse Gossip Fees & Direction-Blind RGB Gossip Causing `min_cost_flow` Path Fragmentation

#### Root Cause
Two subtle behaviors in `rgb-lightning-node` interact during reverse routing (`judy -> alice`):
1. **One-Way Fee Configuration on `/openchannel`**: When node $A$ opens a channel to node $B$ with `fee_base_msat: F`, that fee $F$ only applies to the $A \to B$ direction (`channel_update` flags `direction = 0/1` owned by $A$). Node $B$'s reverse `channel_update` ($B \to A$) always uses LDK's hardcoded default `forwarding_fee_base_msat = 1000` msat.
2. **Direction-Blind RGB Gossip (`htlc_maximum_rgb`)**: In RLN's custom gossip extension, `channel_update` messages advertise `htlc_maximum_rgb` equal to the total initial RGB capacity of the channel in **both** directions, regardless of which peer currently holds the RGB units! Only a node's **direct local channels** (`first_hop_targets` in `routes.rs`) are filtered by real local RGB balance (`available_outbound_rgb`). Remote multi-hop edges in `network_graph` appear to have full RGB capacity in both directions.
3. In `topology.json`, all cross-channels (`bob-frank`, `carol-erin`, `dave-frank`, `grace-ivan`) were opened in the forward direction (`from` closer to `alice`, `to` closer to `judy`). Thus, in the reverse direction (`judy -> alice`), those cross-channels had the default reverse fee of **`1000` msat**—the exact same fee as the main corridors (`heidi -> erin -> bob -> alice` and `ivan -> frank -> carol -> alice`)!
4. When `judy` ran `min_cost_flow` for `03-rgb-return-100` (`100` RGB), the solver saw zero cost penalty for detours across `20`-RGB cross-links (`judy -> heidi -> erin -> carol -> alice`) and decomposed the `100` RGB flow into 3–4 paths (`40 + 40 + 20`), which failed the `max_paths = 2` constraint (`Min-cost flow requires too many HTLC paths`).

#### Exact Fix (`topology-mpp.json`)
- Oriented the 4 cross-channels (`bob-frank`, `carol-erin`, `dave-frank`, `grace-ivan`) with `from` on the downstream node (`frank -> bob`, `erin -> carol`, `frank -> dave`, `ivan -> grace`) and `fee_base_msat: 5000`.
- Because `from -> to` is now in the `judy -> alice` direction, those cross-edges carry a **`5,000` msat fee** during `judy -> alice` return payments, while the two main return corridors (`judy -> heidi -> erin -> bob -> alice` and `judy -> ivan -> frank -> carol -> alice`) carry the `1,000` msat reverse default fee (`3,000` msat per 4-hop corridor).
- `min_cost_flow` now strictly prefers the 2 main corridors (`60 + 40 = 100` RGB, `2` paths, `6,000` msat total fee) without fragmenting across cross-links.

---

### 2.4. Bug 4: Gossip Convergence & Peer Reconnect Race Before Scenario 01

#### Root Cause
After opening 15 channels and mining confirmation blocks, LDK nodes broadcast `channel_announcement` and bidirectional `channel_update` messages asynchronously over P2P. If the benchmark runner starts Scenario `01` or `02` before every sender's local `network_graph` has received all 15 channels and 30 directional `channel_update`s (or if a P2P connection drops momentarily), multi-hop routing fails with `Failed to find route` even though all channels are online.

#### Exact Fix
1. Added `GET /benchmark/gossip-status` in `air-gb-lightning/wallet/apps/local-service/server.ts` (and inspected LDK logs / `/networkinfo` in `rgb-lighning-stats-no-ai`), which parses `.ldk/network_graph` and returns `{ channels, directionalUpdates }`.
2. Added `assertPaymentReady(config, scenarios)` in both `rgb-lighning-stats-no-ai/benchmark/run.mjs` and `air-gb-lightning/wallet/infra/benchmark/run.mjs`:
   - Before executing Case 01, iterates over all active nodes, ensures all channel peers are connected in `/listpeers` (calling `/connectpeer` if any peer disconnected), and polls until all 15 channels are `ready: true` and `is_usable: true`.

---

### 2.5. Bug 5: QVAC Small-Model (`0.8B`) Hallucination Guardrails in `air-gb-lightning`

#### Root Cause
`air-gb-lightning` uses a fast local `0.8B` parameter model (`Qwen3.5-0.8B-Q4_K_M`, alias `qwen-fast`) via QVAC for two sequential tasks per payment:
1. **Channel Scoring (`scoreChannelsWithQvac` in `channel-scoring.ts`)**
2. **Routing Strategy Selection (`selectRoutingAlgorithmWithQvac` in `routing-selector.ts`)**

During initial live runs:
- **Issue A (False `insufficient_liquidity` Classification)**: For an MPP payment of `100` RGB (`02-rgb-mpp-100`), `alice`'s local channels had `60`, `40`, and `40` RGB. Although `deterministicChannelFallback` correctly recognized that `60 < 100` is a valid partial MPP contributor (`good` / `usable_with_penalty`), the `0.8B` LLM saw `assetLocalAmount: "60" < assetAmount: "100"` and classified all three channels as `insufficient_liquidity`! That caused `sanitizeAndValidateChannelScore` to add `bob`, `carol`, and `dave` to `excludedNodeIds`, blocking all of `alice`'s outbound channels!
- **Issue B (Zero-RGB Channels Inflating `mppContributors`)**: In `EphemeralRoutingContext` (`routing-selector.ts`), `mppContributors` filtered channels by `ch.maxSafeMsat > 0n` even when `isRgb` was `true`. If a node had 2 channels with RGB (`60` and `40`) and 1 channel with `0` RGB (e.g., `judy` or `alice` after depletion), `mppContributors.length` was `3`, causing `recommendedMaxPaths` to be set to `3` instead of `2`.
- **Issue C (`min_cost_flow` `max_paths` Over-Splitting & Privacy Override)**: When `algorithm === 'min_cost_flow'` was paired with `max_paths = 3` or `4`, the Rust MCF solver could split flows across 3 paths even when 2 paths sufficed; or when `privacyRequest === 'avoid_extra_probing'` (`18-rgb-privacy`), the LLM could still propose `min_cost_flow` with `max_paths > 1`.

#### Exact Fix (`channel-scoring.ts` & `routing-selector.ts`)
1. In `wallet/apps/local-service/channel-scoring.ts`:
   ```ts
   const fallback = deterministicChannelFallback(channel, input);
   const rawClass = item?.classification && (CHANNEL_CLASSIFICATIONS as readonly string[]).includes(item.classification)
     ? item.classification : fallback.classification;
   // Guardrail: If deterministic physical check confirms the channel has sufficient liquidity,
   // do not allow the 0.8B model to hallucinate insufficient_liquidity or avoid.
   const classification = (fallback.classification !== 'insufficient_liquidity' && fallback.classification !== 'avoid'
     && (rawClass === 'insufficient_liquidity' || rawClass === 'avoid'))
     ? fallback.classification
     : rawClass;
   ```
2. In `wallet/apps/local-service/routing-selector.ts`:
   - Excluded zero-RGB channels from `mppContributors` when `isRgb` is true:
     ```ts
     const mppContributors = usable.filter(ch => ch.maxSafeMsat > 0n && (!isRgb || ch.maxSafeAssetAmount > 0n));
     ```
   - Enforced deterministic post-LLM guardrails in `selectRoutingAlgorithmWithQvac`:
     - If `privacySinglePath` (`compiledPolicy?.fallbackHint?.preferSinglePath === true`), force `max_paths = 1` and downgrade `min_cost_flow` to `multi_objective`.
     - If `singlePathFeasible` is true, force `max_paths = 1` and use `multi_objective` (preventing unnecessary MPP splits when a single local channel can carry the full payment).
     - If `!singlePathFeasible && mppFeasible`, upgrade to `min_cost_flow` and cap `max_paths = Math.min(2, mppMaxPaths)`.

---

## 3. Declarative Topology Specification (`topology.json` vs. `topology-mpp.json`)

Both topologies define **20 nodes** (`alice`..`tara`) and **15 channels** connecting the **10 core nodes** (`alice`..`judy`), while leaving the **10 peripheral nodes** (`karl`..`tara`) with `0` channels as negative reachability controls.

### 3.1. Channel-by-Channel Comparison Table

All channels have:
- `capacity_sat = 1,000,000` (`1,000,000,000` msat)
- `asset = "TEST"` (ticker `ROUTE`, precision `0`, supply `10000`)
- `fee_proportional_millionths = 0`
- Reverse direction (`to -> from`) base fee: **`1,000` msat** (LDK default)

| Channel ID | `topology.json` (`from -> to`) | `topology.json` RGB | `topology.json` Fee (`from->to`) | `topology.json` `push_msat` | `topology-mpp.json` (`from -> to`) | `topology-mpp.json` RGB | `topology-mpp.json` Fee (`from->to`) | `topology-mpp.json` `push_msat` | Engineering Purpose of Change in `topology-mpp.json` |
| :--- | :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :--- |
| `alice-bob` | `alice -> bob` | `60` | `0` | `0` | `alice -> bob` | `60` | `0` | `7,000,000` | Upper 60-RGB branch ingress + 7k sat reserve cushion |
| `alice-carol` | `alice -> carol` | `40` | `0` | `0` | `alice -> carol` | `40` | `0` | `7,000,000` | Middle 40-RGB branch ingress + 7k sat reserve cushion |
| `alice-dave` | `alice -> dave` | `40` | `0` | `0` | `alice -> dave` | `40` | `0` | `7,000,000` | Lower 40-RGB branch ingress + 7k sat reserve cushion |
| `bob-erin` | `bob -> erin` | `60` | `1,000` | `0` | `bob -> erin` | `60` | **`200`** | `7,000,000` | Upper 60-RGB corridor (`600` msat total 4-hop forward fee) |
| `erin-heidi` | `erin -> heidi` | `60` | `1,000` | `0` | `erin -> heidi` | `60` | **`200`** | `7,000,000` | Upper 60-RGB corridor (`200` msat/hop) |
| `heidi-judy` | `heidi -> judy` | `60` | `1,000` | `0` | `heidi -> judy` | `60` | **`200`** | `7,000,000` | Upper 60-RGB egress into `judy`; `push_msat` unlocks `judy -> heidi` |
| `carol-frank` | `carol -> frank` | `40` | `2,000` | `0` | `carol -> frank` | `40` | **`100`** | `7,000,000` | Middle 40-RGB low-fee corridor (`300` msat total 4-hop forward fee) |
| `frank-ivan` | `frank -> ivan` | `40` | `2,000` | `0` | `frank -> ivan` | `40` | **`100`** | `7,000,000` | Middle 40-RGB low-fee corridor (`100` msat/hop) |
| `ivan-judy` | `ivan -> judy` | `40` | `2,000` | `0` | `ivan -> judy` | `40` | **`100`** | `7,000,000` | Middle 40-RGB egress into `judy`; `push_msat` unlocks `judy -> ivan` |
| `dave-grace` | `dave -> grace` | `40` | `3,000` | `0` | `dave -> grace` | `40` | **`5,000`** | `7,000,000` | Direct `dave -> grace` channel for Case `14-rgb-dave-grace` |
| `bob-frank` | `bob -> frank` | `20` | `4,000` | `0` | **`frank -> bob`** | `20` | **`5,000`** | `7,000,000` | Penalizes `frank -> bob` (`5,000` msat) during `judy -> alice` return |
| `carol-erin` | `carol -> erin` | `20` | `3,000` | `0` | **`erin -> carol`** | `20` | **`5,000`** | `7,000,000` | Penalizes `erin -> carol` (`5,000` msat) during `judy -> alice` return |
| `dave-frank` | `dave -> frank` | `20` | `4,000` | `0` | **`frank -> dave`** | `20` | **`5,000`** | `7,000,000` | Penalizes `frank -> dave` (`5,000` msat) during `judy -> alice` return |
| `grace-ivan` | `grace -> ivan` | `40` | `3,000` | `0` | **`ivan -> grace`** | `40` | **`5,000`** | `7,000,000` | Penalizes `ivan -> grace` (`5,000` msat) during `judy -> alice` return |
| `frank-heidi` | `frank -> heidi` | `20` | `5,000` | `0` | **`heidi -> frank`** | `20` | **`5,000`** | `7,000,000` | Gives `heidi` outbound RGB to `frank` for `18-rgb-privacy` (`heidi -> ivan`) |

---

## 4. Complete 20-Scenario Workload Specification (`scenarios.json`)

The 20 scenarios execute **sequentially without resetting balances**. Thus, every payment's outcome mutates the live channel state for all subsequent payments.

| Order | Case ID | Category | Sender | Recipient | `amountMsat` | Asset | `assetAmount` | User Prompt (Natural Language Policy Input) | Physical / Graph Feasibility Role |
| :---: | :--- | :--- | :--- | :--- | :---: | :---: | :---: | :--- | :--- |
| `1` | `01-btc-direct` | `btc_direct` | `alice` | `bob` | `1,000,000` | `null` | `null` | `"Pagamento BTC diretto veloce e con fee minime"` | Positive control: 1-hop direct BTC payment (`0` fee). |
| `2` | `02-rgb-mpp-100` | `rgb_mpp_full` | `alice` | `judy` | `30,000,000` | `TEST` | `100` | `"Invia 100 TEST a Judy, puoi dividere il pagamento su piu percorsi ma mantieni le fee ragionevoli"` | **Requires 2-path MPP (`60 + 40`)**: `judy`'s only inbound channels are `heidi-judy` (`60` RGB) and `ivan-judy` (`40` RGB). Impossible for single-path (`max 60`). |
| `3` | `03-rgb-return-100` | `rgb_rebalance_return` | `judy` | `alice` | `10,000,000` | `TEST` | `100` | `"Rimanda 100 TEST ad Alice usando piu percorsi se necessario per riequilibrare la liquidita"` | **Requires 2-path MPP (`60 + 40`) in reverse**: replenishes `alice`'s RGB liquidity if `02` succeeded and `push_msat` clears LDK reserve. |
| `4` | `04-rgb-mpp-80` | `rgb_mpp_partial` | `alice` | `judy` | `30,000,000` | `TEST` | `80` | `"Invia 80 TEST a Judy, consenti multi-path con attenzione alle fee"` | **Requires 2-path MPP (`40 + 40` or `60 + 20`)**: `80 > 60` max single branch. Only possible if `03` returned RGB to `alice`. |
| `5` | `05-rgb-return-80` | `rgb_rebalance_return` | `judy` | `alice` | `10,000,000` | `TEST` | `80` | `"Restituisci 80 TEST ad Alice dividendo il pagamento se serve"` | Reverse 80 RGB MPP flow. Exposes RLN's direction-blind `htlc_maximum_rgb` gossip limitation when `heidi-judy` has `40 < 60` RGB. |
| `6` | `06-rgb-over-capacity` | `rgb_over_capacity` | `alice` | `judy` | `30,000,000` | `TEST` | `101` | `"Prova a inviare 101 TEST a Judy anche dividendo il pagamento"` | **Negative Control #1**: Total network RGB capacity into `judy` is `100`. Must fail on both stacks. |
| `7` | `07-rgb-single-boundary` | `rgb_single_boundary` | `alice` | `judy` | `10,000,000` | `TEST` | `60` | `"Invia 60 TEST a Judy evitando split inutili se un percorso basta"` | Single-path boundary (`60` RGB via `bob-erin-heidi`). Succeeds in baseline only because `02` and `04` failed and left `alice-bob` untouched. |
| `8` | `08-rgb-return-60` | `rgb_rebalance_return` | `judy` | `alice` | `8,000,000` | `TEST` | `60` | `"Rimanda 60 TEST ad Alice con percorso affidabile"` | Single-path return of `60` RGB (`judy -> heidi -> erin -> bob -> alice`). Succeeds in AI (`ai-02`), replenishing `alice-bob`. |
| `9` | `09-rgb-low-fee` | `rgb_fee_sensitive` | `alice` | `judy` | `10,000,000` | `TEST` | `30` | `"Invia 30 TEST a Judy minimizzando le fee"` | Single-path `30` RGB transfer. |
| `10` | `10-rgb-return-30` | `rgb_rebalance_return` | `judy` | `alice` | `8,000,000` | `TEST` | `30` | `"Restituisci 30 TEST ad Alice minimizzando le fee"` | Single-path return of `30` RGB (`judy -> alice`). Succeeds in AI (`ai-02`), restoring `30` more RGB to `alice-bob`. |
| `11` | `11-rgb-direct` | `rgb_min_htlc` | `alice` | `bob` | `3,000,000` | `TEST` | `1` | `"Invia 1 TEST a Bob sul percorso piu semplice e affidabile"` | **Demonstrates downstream liquidity multiplier**: AI pays `alice -> bob` in 1 direct hop (`0` msat fee) because `08`+`10` returned RGB. Baseline (`stock-02`) drained `alice-bob` in `07` and takes a 3-hop detour (`alice -> carol -> erin -> bob`) costing **`5,100` msat**! |
| `12` | `12-rgb-bob-erin` | `rgb_intermediate_sender` | `bob` | `erin` | `5,000,000` | `TEST` | `10` | `"Invia 10 TEST a Erin privilegiando canali stabili"` | Direct 1-hop RGB transfer (`0` msat). Succeeds in AI (`ai-02`); fails in Baseline (`bob-erin` was drained by `07`). |
| `13` | `13-rgb-carol-frank` | `rgb_intermediate_sender` | `carol` | `frank` | `5,000,000` | `TEST` | `15` | `"Invia 15 TEST a Frank con fee basse"` | Direct 1-hop RGB transfer (`0` msat). Succeeds in AI (`ai-02`); fails in Baseline (`carol-frank` had only `9` RGB left after `09` and `11`). |
| `14` | `14-rgb-dave-grace` | `rgb_high_fee_branch` | `dave` | `grace` | `5,000,000` | `TEST` | `15` | `"Invia 15 TEST a Grace, accetta il ramo disponibile anche se costa di piu"` | Direct 1-hop RGB transfer on `dave-grace` (`0` msat sender fee). Both succeed. |
| `15` | `15-rgb-frank-ivan` | `rgb_intermediate_sender` | `frank` | `ivan` | `5,000,000` | `TEST` | `20` | `"Invia 20 TEST a Ivan privilegiando affidabilita"` | Direct 1-hop RGB transfer (`0` msat). Succeeds in AI (`ai-02`) because `13` replenished `frank`; fails in Baseline (`frank-ivan` had only `10` RGB left). |
| `16` | `16-btc-multihop` | `btc_multihop` | `alice` | `judy` | `10,000,000` | `null` | `null` | `"Invia 10000 sat a Judy in BTC minimizzando le fee"` | 4-hop BTC transfer (`alice -> judy`). Both succeed. |
| `17` | `17-btc-large` | `btc_mpp` | `alice` | `judy` | `1,200,000,000` | `null` | `null` | `"Invia 1200000 sat a Judy in BTC, dividi su piu canali se necessario"` | **Negative Control #2**: `1.2B` msat exceeds LDK's default `max_in_flight_htlc_percent = 10%` (`100M` msat per channel). Both fail. |
| `18` | `18-rgb-privacy` | `rgb_privacy_single_path` | `heidi` | `ivan` | `10,000,000` | `TEST` | `10` | `"Invia 10 TEST a Ivan evitando probing extra e preferendo un solo percorso"` | Tests privacy constraint (`avoid_extra_probing` $\rightarrow$ `max_paths = 1`) over `heidi -> frank -> ivan`. |
| `19` | `19-btc-peripheral` | `peripheral_reachability` | `karl` | `tara` | `3,000,000` | `null` | `null` | `"Invia 3000 sat a Tara se esiste una rotta disponibile"` | **Negative Control #3**: `karl` and `tara` have `0` channels. Both fail. |
| `20` | `20-rgb-peripheral` | `peripheral_reachability` | `leo` | `saul` | `3,000,000` | `TEST` | `1` | `"Invia 1 TEST a Saul se raggiungibile"` | **Negative Control #4**: `leo` and `saul` have `0` channels. Both fail. |

---

## 5. Detailed Experimental Results (`comparison-02` on `topology-mpp.json`)

### 5.1. Initial State Verification
- **Baseline Run ID**: `stock-02` (`/home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai/results/benchmark/stock-02`)
- **AI + MPP Run ID**: `ai-02` (`/home/pesce/GitLab_Projects/air-gb-lightning/wallet/native-data/benchmarks/ai-02`)
- **Initial State Hash (SHA-256 of normalized directional channel balances)**:
  `fc59bc783b32d65f2afada257571a5351de2c91b1ed748ecd10cc4c2432d8b45` (Identical across both runs; `comparable: true`).

### 5.2. Aggregate Metrics (`comparison-02/comparison.json`)
- **Total Scenarios**: `20` (`16` physically reachable + `4` negative controls)
- **Baseline (`stock-02`) Successes**: `7 / 20` (`35.0%` overall, `43.75%` of reachable)
- **AI + MPP (`ai-02`) Successes**: `13 / 20` (`65.0%` overall, `81.25%` of reachable)
- **Success Rate Delta**: **`+30.0` percentage points** (`+85.7%` relative increase in completed payments)
- **AI-Only Successes (8 cases)**: `02-rgb-mpp-100`, `03-rgb-return-100`, `04-rgb-mpp-80`, `08-rgb-return-60`, `10-rgb-return-30`, `12-rgb-bob-erin`, `13-rgb-carol-frank`, `15-rgb-frank-ivan`
- **Baseline-Only Successes (2 cases)**: `07-rgb-single-boundary`, `18-rgb-privacy`
- **Paired Successes (5 cases where both succeeded)**: `01-btc-direct`, `09-rgb-low-fee`, `11-rgb-direct`, `14-rgb-dave-grace`, `16-btc-multihop`
  - **Baseline Total Fee on Paired Successes**: `5,700` msat
  - **AI + MPP Total Fee on Paired Successes**: `1,200` msat
  - **Matched-Pair Fee Saving**: **`4,500` msat (`78.947%` fee reduction)**
  - **Matched-Pair Mean Hop Delta**: **`-0.4` hops** (AI averaged `2.2` hops vs. Baseline `2.6` hops on paired successes)

### 5.3. Per-Case Telemetry & Timing Breakdown (`ai-02` vs. `stock-02`)

| Case ID | `stock-02` Status | `ai-02` Status | `ai-02` QVAC Raw Proposal (`algo` / `max_paths`) | `ai-02` Final Guardrailed (`algo` / `max_paths`) | `stock-02` Fee (msat) | `ai-02` Fee (msat) | `stock-02` Hops (Parts) | `ai-02` Hops (Parts) | `ai-02` Selection (`ms`) | `stock-02` Settlement (`ms`) | `ai-02` Settlement (`ms`) |
| :--- | :---: | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `01-btc-direct` | `succeeded` | `succeeded` | `ldk_dijkstra` (1) | `multi_objective` (1) | `0` | `0` | `1` (1) | `1` (1) | `32,053` | `3,848` | `2,073` |
| `02-rgb-mpp-100` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `min_cost_flow` (2) | — | `900` | — | `8` (2) | `34,405` | `135` | `17,853` |
| `03-rgb-return-100` | `failed` | **`succeeded`** | `ldk_dijkstra` (2) | `min_cost_flow` (2) | — | `6,000` | — | `8` (2) | `29,432` | `102` | `17,121` |
| `04-rgb-mpp-80` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `min_cost_flow` (2) | — | `900` | — | `8` (2) | `28,159` | `132` | `19,179` |
| `05-rgb-return-80` | `failed` | `failed` | `ldk_dijkstra` (1) | `min_cost_flow` (2) | — | — | — | — | `27,484` | `101` | `11,226` |
| `06-rgb-over-capacity` | `failed` | `failed` | `ldk_dijkstra` (2) | `multi_objective` (1) | — | — | — | — | `25,383` | `124` | `11,254` |
| `07-rgb-single-boundary` | **`succeeded`** | `failed` | `ldk_dijkstra` (1) | `min_cost_flow` (2) | `600` | — | `4` (1) | — | `27,201` | `13,121` | `11,264` |
| `08-rgb-return-60` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `multi_objective` (1) | — | `3,000` | — | `4` (1) | `26,113` | `101` | `17,677` |
| `09-rgb-low-fee` | `succeeded` | `succeeded` | `ldk_dijkstra` (1) | `multi_objective` (1) | `300` | `600` | `4` (1) | `4` (1) | `28,287` | `12,188` | `17,910` |
| `10-rgb-return-30` | `failed` | **`succeeded`** | `ldk_dijkstra` (2) | `multi_objective` (1) | — | `3,000` | — | `4` (1) | `26,396` | `97` | `18,645` |
| `11-rgb-direct` | `succeeded` | `succeeded` | `ldk_dijkstra` (2) | `multi_objective` (1) | `5,100` | **`0`** | `3` (1) | **`1` (1)** | `27,253` | `10,164` | **`3,082`** |
| `12-rgb-bob-erin` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `multi_objective` (1) | — | `0` | — | `1` (1) | `26,706` | `104` | `3,074` |
| `13-rgb-carol-frank` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `multi_objective` (1) | — | `0` | — | `1` (1) | `26,766` | `102` | `3,083` |
| `14-rgb-dave-grace` | `succeeded` | `succeeded` | `ldk_dijkstra` (1) | `multi_objective` (1) | `0` | `0` | `1` (1) | `1` (1) | `23,040` | `3,925` | `2,086` |
| `15-rgb-frank-ivan` | `failed` | **`succeeded`** | `ldk_dijkstra` (1) | `multi_objective` (1) | — | `0` | — | `1` (1) | `25,650` | `104` | `3,075` |
| `16-btc-multihop` | `succeeded` | `succeeded` | `ldk_dijkstra` (1) | `multi_objective` (1) | `300` | `600` | `4` (1) | `4` (1) | `28,176` | `3,023` | `19,862` |
| `17-btc-large` | `failed` | `failed` | `ldk_dijkstra` (1) | `min_cost_flow` (2) | — | — | — | — | `27,823` | `104` | `11,231` |
| `18-rgb-privacy` | **`succeeded`** | `failed` | `ldk_dijkstra` (1) | `multi_objective` (1) | `100` | — | `2` (1) | — | `26,731` | `6,349` | `11,173` |
| `19-btc-peripheral` | `failed` | `failed` | `ldk_dijkstra` (1) | `multi_objective` (1) | — | — | — | — | `18,142` | `124` | `11,148` |
| `20-rgb-peripheral` | `failed` | `failed` | `ldk_dijkstra` (1) | `multi_objective` (1) | — | — | — | — | `21,701` | `115` | `11,150` |

---

## 6. Reproducible Execution Commands

To reproduce the isolated sequential benchmark from scratch without resource contention:

### Step 1: Run Baseline (`rgb-lighning-stats-no-ai`)
```bash
cd /home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai

# Ensure AI stack is stopped before running Baseline
docker compose -f /home/pesce/GitLab_Projects/air-gb-lightning/docker-compose.yml \
  -f /home/pesce/GitLab_Projects/air-gb-lightning/docker-compose.topology.yml down || true

# Bootstrap 20-node baseline topology with topology-mpp.json
python3 benchmark/setup.py up --project rgb-bench --topology benchmark/topology-mpp.json

# Execute the 20 sequential scenarios
node benchmark/run.mjs --config results/benchmark/stock-02-config.json --out results/benchmark/stock-02

# Tear down baseline containers to free resources
python3 benchmark/setup.py down --project rgb-bench --volumes
```

### Step 2: Run AI + MPP (`air-gb-lightning`)
```bash
cd /home/pesce/GitLab_Projects/air-gb-lightning

# Start QVAC local LLM + TurboVec RAG server on host
npm --prefix wallet run qvac:serve &

# Start 20-node AIR-GB Compose stack with benchmark telemetry overlay
set -a && source wallet/native-data/regtest/wallet.env && set +a
docker compose -f docker-compose.yml -f docker-compose.topology.yml \
  -f wallet/infra/benchmark/compose.observations.yml up -d

# Bootstrap wallets, issue RGB asset, open 15 channels from topology-mpp.json, and wait for gossip convergence
node wallet/infra/benchmark/bootstrap-topology.mjs --topology topology-mpp.json \
  --config-out wallet/native-data/benchmark-ai-mpp.json

# Execute the 20 sequential scenarios
node --import tsx wallet/infra/benchmark/run.mjs \
  --config wallet/native-data/benchmark-ai-mpp.json \
  --topology topology-mpp.json \
  --out wallet/native-data/benchmarks/ai-02
```

### Step 3: Compare Both Runs
```bash
cd /home/pesce/GitLab_Projects/rgb-lighning-stats-no-ai
node benchmark/compare.mjs \
  --stock results/benchmark/stock-02 \
  --ai /home/pesce/GitLab_Projects/air-gb-lightning/wallet/native-data/benchmarks/ai-02 \
  --out results/benchmark/comparison-02
```
