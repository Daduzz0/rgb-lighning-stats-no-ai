# AIR-GB Lightning — Presenter Guide

Companion to `AIR-GB-Lightning-Pitch-Deck-v3.pdf` (v2 has the same slides and wording, minus the monospace detail lines).

## Time budget (10 minutes)

| Block | Slides | Time |
| --- | --- | --- |
| Pitch | 1–8 | 5:00 (about 35–40 s per slide) |
| Live demo | 9 | 2:00 |
| Q&A | 9 stays on screen | 3:00 |

Rules of thumb:

- Say the slide title's idea in the first sentence, then the one number that proves it. Stop there.
- The grey monospace lines (v3) are for the judges to read, not for you to recite. Mention them only if you have spare time or someone asks.
- Tone toward RGB / rgb-lightning-node: we **build on** it. Never say "broken", "bug", "fails at scale". Say "single-path by default", "we added", "we extended".

---

## Slide 1 — Title (0:00–0:30)

**Goal:** who you are, what it is, three numbers.

**Say:**

> "Hi, I'm [name]. This is AIR-GB Lightning: multi-path, intent-aware routing for RGB assets on Lightning.
> The idea in one line: a small local AI proposes how to route, deterministic code verifies it, and Rust does the routing.
> On a 20-payment benchmark, we went from 35% to 65% of payments completed, paid 79% less in fees where both versions succeeded, and route planning takes about a millisecond on the node."

**Numbers to know cold:** 7/20 → 13/20 · 5,700 → 1,200 msat · 0.66–1.35 ms.

**Transition:** "Let me start with why multi-path matters."

---

## Slide 2 — Why multi-path matters (0:30–1:10)

**Goal:** make the 60 + 40 = 100 picture land. This is the core intuition of the whole pitch.

**Say:**

> "Alice wants to pay Judy 100 RGB. She has 100, but split across two channels: 60 on one, 40 on the other.
> If a payment can only take one path, the most she can send is 60. If it can split, both channels together deliver the full 100.
> As RGB channels multiply, liquidity naturally gets spread like this, so being able to combine it matters more and more.
> Two more things come with it: return flows keep channels balanced, so later payments stay direct and cheap; and users can express intent — fee caps, nodes to avoid, privacy — in plain language."

**Avoid:** criticising the stock node. Frame it as "single path by default, we add multi-path on top".

**Transition:** "Here's how a payment flows through the system."

---

## Slide 3 — How a payment gets routed (1:10–1:50)

**Goal:** show the 4-stage pipeline next to the standard flow. Don't explain every stage in depth — slides 4–6 do that.

**Say:**

> "Top row is the standard flow: one request, one shortest path, send. That got 7 out of 20 in our test.
> Bottom row is AIR-GB, four stages: first we understand the user's intent and compile it into a strict policy. Then local AI recalls past payments and scores each channel. Then deterministic guardrails verify everything and pick a strategy — single path or multi-path. Finally Rust plans the actual route, with four graph planners including min-cost flow for splitting.
> Same network, 13 out of 20."

**If asked about the planners:** min_cost_flow (splits), multi_objective (Pareto trade-off of fee / reliability / CLTV / hops), yen (K-shortest paths), ldk_dijkstra (fallback). In the benchmark only min_cost_flow and multi_objective were actually chosen.

**Transition:** "Let's open stage two — the AI."

---

## Slide 4 — Two small local models (1:50–2:30)

**Goal:** the AI is small, local, and has narrow jobs. Defuse "AI + money = scary".

**Say:**

> "We use two small models, both running locally on the node through QVAC.
> EmbeddingGemma, 300 million parameters, is the memory: after every payment it stores a short summary of what happened, and before the next one it recalls the four most similar past payments.
> Qwen 3.5 at 0.8 billion parameters is the reasoning: it reads the user's request, scores each channel using that memory, and suggests an algorithm and how many parts to split into. Temperature zero, strict JSON output.
> Everything stays on the node: no cloud calls, the AI never touches keys, and it only stores the node's own data — nothing about other people's channels."

**Key phrase:** "small models, narrow jobs, fully local."

**Transition:** "But a 0.8B model will make mistakes. So the AI never gets the final say."

---

## Slide 5 — The AI suggests. Code decides. (2:30–3:10)

**Goal:** the safety story. This is the slide that answers "what if the model hallucinates?" before anyone asks.

**Say:**

> "Every model output goes through four deterministic checks before anything is sent.
> One: the output format is locked — the model can only name channels that actually exist right now.
> Two: anything it extracts, like a fee cap or a node name, must literally appear in what the user wrote, and the final policy is cryptographically bound to the invoice.
> Three: real balances always win — if the model says a channel is fine but it doesn't have the liquidity, code overrides it, and the other way round.
> Four: strategy rules are fixed — if one path can't carry the payment, we split; if the user asks for privacy, we use one path.
> So a model mistake gets caught before any payment is sent."

**Transition:** "Now the part that actually moves the money: multi-path in Rust."

---

## Slide 6 — One payment, two paths (3:10–3:55)

**Goal:** walk the 100 RGB payment across the diagram. Point at it while talking.

**Say:**

> "Here's the real benchmark payment: Alice sends Judy 100 RGB.
> First we check which channels can actually carry RGB and work out the minimum number of parts.
> Then min-cost flow fills the cheaper path first — 40 RGB through Carol, Frank and Ivan at 100 msat per hop — and the remaining 60 through Bob, Erin and Heidi.
> Each part also carries its share of the bitcoin needed for the HTLC: 12 and 18 million msat.
> And Judy only claims the payment when all 100 RGB and all the bitcoin have arrived. Either everything settles or nothing does. Total fee: 900 msat."

**If asked "what did you change in the node?":** we extended rgb-lightning-node / rust-lightning so multi-path works with RGB: the RGB amount is tracked per part, the receiver waits for both the RGB and BTC totals before releasing the preimage, and the RGB amount committed to each HTLC is computed correctly when several are in flight at once. Present it as "what multi-path needed", not as fixing someone's bug.

**Transition:** "To measure all this, we built a test network."

---

## Slide 7 — Test network (3:55–4:30)

**Goal:** credibility of the benchmark. Keep it short.

**Say:**

> "Twenty nodes on regtest. The ten connected ones form two parallel corridors from Alice to Judy — 60 RGB on top, 40 in the middle — plus some side channels and expensive cross links.
> The other ten nodes are isolated on purpose: payments to them must fail, so they check we don't report false successes.
> Both versions start from exactly the same channel state, verified by hash, and run the same 20 payments in sequence."

**Why in sequence matters:** each payment changes balances for the next. That's realistic, and it's why return flows matter so much.

**Transition:** "Here's what happened."

---

## Slide 8 — Results (4:30–5:05)

**Goal:** the punchline. Two numbers, then where they come from.

**Say:**

> "Same network, same 20 payments. 7 completed with the baseline, 13 with AIR-GB — plus 30 points. Out of the 16 payments that were actually possible, that's 81%.
> Where does it come from? All three multi-path payments go through. 190 RGB flow back to Alice instead of zero, so the network stays balanced, and that unlocks the three mid-network payments after that. And both versions correctly reject the four impossible payments.
> On fees: on the payments both versions completed, we paid 1,200 msat instead of 5,700. Most of that is case 11 — the baseline had to detour 3 hops for 5,100 msat, while we went direct for free because the channel had been refilled."

**Transition:** "Let me show it live."

---

## Slide 9 — Live demo (5:05–7:05) + Q&A (7:05–10:00)

**Before you start:** web UI open on `http://127.0.0.1:5173`, QVAC server running, regtest nodes up, gossip settled. Have a fallback recording or screenshots ready in case the stack misbehaves.

**Demo script (about 40 s per step):**

1. **Ask in plain words.** Type something like *"Max 1,000 msat fee, and avoid Bob."* Show the compiled policy: fee cap, excluded node, and the SHA-256 fingerprint tying it to the invoice.
   > "The model extracted two constraints, code verified both appear in my sentence, and the policy is now bound to this exact invoice."
2. **See the AI's view.** Show the channel scores (optimal / good / congested / avoid) built from the node's own payment memory.
   > "These scores come from the node's own history — and if a score disagreed with the real balance, code would have overridden it."
3. **Send it.** Send a payment that needs splitting. Show `min_cost_flow` with `max_paths = 2` and the payment completing in two parts.
   > "One path couldn't carry it, so the guardrails forced a split. Two parts, settled together."

**Close:**

> "To sum up: 35 to 65% success, 79% lower fees, and all the AI runs locally on the node. Thank you — happy to take questions."

Leave slide 9 up during Q&A.

---

## Q&A prep

**"Isn't the fee win mostly one case?"**
Yes, and say so. Five payments completed in both runs (01, 09, 11, 14, 16). Case 11 accounts for 5,100 of the 5,700 msat. On 09 and 16 AIR-GB actually paid a bit more (600 vs 300 msat) because it chose a different route. The headline result is success rate; fees are a side effect of keeping channels balanced.

**"Did AIR-GB fail anything the baseline passed?"**
Yes: cases 07 and 18. Because the run is sequential, AIR-GB had already moved liquidity in earlier payments that the baseline failed, so the channel state at 07 and 18 was different. Case 05 (an 80 RGB return) also failed for AIR-GB. Being upfront about this builds trust.

**"What if the LLM hallucinates?"**
Slide 5. It can only output channel IDs that exist, its extractions must appear in the user's text, real balances override its scores, and strategy rules are fixed in code. The worst it can do is suggest a worse-but-valid route.

**"Why such small models?"**
They must run on the node itself: no cloud, low latency, no data leaving the machine. The jobs are narrow (extraction, classification, choosing from a short list), and the guardrails cover the error rate.

**"Does the AI see other people's data?"**
No. Only the node's own payment outcomes are stored; anything about third-party hops or remote balances is rejected. Per-request routing data is wiped from memory after each decision.

**"How fast is it?"**
Rust route planning: 0.66–1.35 ms. The LLM calls dominate end-to-end time (seconds), which is acceptable for payments where the user is waiting on a confirmation anyway, and the deterministic path works without the AI.

**"What did you change in rgb-lightning-node / rust-lightning?"**
Multi-path support for RGB: splitting an RGB amount across parts with its BTC carrier, making the receiver wait for both totals, and correct RGB accounting when several HTLCs are in flight on one channel. Frame it as an extension that we'd like to upstream, not as a fix.

**"Is regtest realistic?"**
It's controlled on purpose: the same starting state, the same payments, and failures that must fail. The next step is a larger, randomised topology and signet.

**"Why not just enable multi-path without the AI?"**
That alone would win the three split payments. The AI part adds intent (fee caps, exclusions, privacy) and memory-based channel scoring. The guardrails make it safe to let a model influence routing at all.
