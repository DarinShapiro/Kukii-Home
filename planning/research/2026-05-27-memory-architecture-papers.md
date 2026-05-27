# Memory architecture research — three papers (2026-05-27)

Cross-reference report from a deep-research pass on three papers, evaluated against the SentiHome memory architecture decisions locked during the Epic 10 design dialogue (see `planning/epics/10-identity-recognition.md`).

**Papers reviewed**:

1. **MemORAI** — Pham Van et al., 2026 (arXiv 2605.01386) — Memory Organization and Retrieval via Adaptive Graph Intelligence for LLM Conversational Agents
2. **Mnemosyne** — Jonelagadda et al., 2025 (arXiv 2510.08601) — Unsupervised, Human-Inspired Long-Term Memory Architecture for Edge-Based LLMs
3. **Trainable Graph Memory** — Xia et al., 2025 (arXiv 2511.07800) — From Experience to Strategy: Empowering LLM Agents with Trainable Graph Memory

---

## Paper 1 — MemORAI

**Authors:** Pham Van, Hieu, Pham Tran Tuan, Le Hai, Ngo Van, Thi Ngoc Diep, Le
**Year:** 2026 (ACL Findings, submitted May 2026)
**arXiv:** 2605.01386

### Summary

MemORAI is a graph-based long-term memory system for LLM conversational agents that targets three failure modes of prior graph memory: information dilution from indiscriminate storage, lack of provenance, and query-blind uniform retrieval. It introduces dual-layer compression (filtered user-relevant turns plus segment summaries), a provenance-enriched multi-relational graph with explicit turn-tracking, and **query-conditioned edge weighting via Dynamic Weighted PageRank**. Reports SOTA on LongMemEval-s (75.55% GPT-4o-J, R@3 session recall 90.17) and LOCOMO-10 (60.22%).

### Key techniques

- **Dual-layer compression**: filter set `M_i ⊆ S_i` (only user-persona-relevant utterances retained) AND segment-level summary `σ_i` (global anchor). Both stored — fine-grained personal content and coarse contextual anchor.
- **Provenance-enriched graph**: three node types — Entity `e ∈ V_E`, Turn `τ ∈ V_T`, Segment `s ∈ V_S`. Entities carry `name`, `description`, `turn_ids`. Edges include entity-relation-entity with `source_turns` arrays, and bidirectional entity-turn and turn-segment edges.
- **Dynamic Weighted PageRank** for retrieval. Edge weight is query-conditioned:
  - `w(u→v) = sim(q, e.desc)` for entity-turn
  - `w(u→v) = sim(q, r.desc)` for entity-relation
  - `w(u→v) = (1/|τ|) Σ sim(q, e.desc)` for turn-segment
- Propagation: `PR_{t+1}(v) = (1−d)·seed(v) + d·S(v)` with `S(v) = Σ_u [w(u→v)/Σ_* w(u→*)]·PR_t(u)`, seeded by semantic similarity to `q`.
- **Incremental graph updates** — no re-encoding needed when memory grows.
- Hyperparameters: top-k=3 (one-hop expansion from seeds), Contriever embeddings for similarity.

### Relevance vs. our 10 open questions

| # | Question | Applies? | Note |
|---|---|---|---|
| 1 | Edge weight reinforcement/decay | partial | Weights are query-derived, not learned/decayed over time. Different paradigm — no temporal weight evolution. |
| 2 | Memory compression heuristics | yes | Dual-layer compression directly maps to our episodic compression problem. |
| 3 | Long-tail protection | partial | Substance filter is binary persona-relevance only; no rarity protection. |
| 4 | Hybrid graph + vector retrieval | **yes — strongly** | DW-PageRank is the most directly transferable idea in this paper. |
| 5 | Memory layer transitions | no | MemORAI does not have working/session/episodic separation. |
| 6 | Identity embedding drift | no | Not addressed. |
| 7 | Multi-modal identity confidence | no | Text-only. |
| 8 | Cross-camera correlation | no | N/A. |
| 9 | Forgetting curve modeling | no | No temporal decay. |
| 10 | Agentic memory dynamics | partial | Comparable to GraphRAG / Zep family but with query-adaptive weights. |

---

## Paper 2 — Mnemosyne

**Authors:** Jonelagadda, Hahn, Zheng, Penachio (Kaliber AI)
**Year:** 2025 (October 7, 2025)
**arXiv:** 2510.08601

### Summary

Mnemosyne is an unsupervised long-term memory system explicitly designed for **resource-constrained edge devices**, with longitudinal healthcare conversation as its target use case. It mirrors human memory: probabilistic recall with temporal decay, refresh/rewind on re-encounter, substance filtering to reject non-meaningful chatter, and redundancy-driven node pairing. Graph storage in-memory plus Redis for embeddings. 65.8% human-eval win rate over naive RAG; second on LoCoMo behind Memory-R1.

### Key techniques

- **Probabilistic recall**: `P(m) = μ · e_nm · τ(e_eff)` where `μ` is exploration parameter (default 2.0), `e_nm` is edge weight, and `τ` is temporal decay.
- **Temporal decay** (reverse sigmoid with linear correction near 0):
  ```
  τ(e_eff) = (1−d) / (1 + e^((e_eff − a)/b))   for c ≤ e_eff
  τ(e_eff) = −e_eff·(1−τ(c))/c                 for 0 ≤ e_eff ≤ c
  ```
  Parameters: midpoint `a = mid_sig = 2,419,200 s` (≈4 weeks), steepness `b`, floor `d = 0.05` (memories never fully forgotten), linear-transition `c`.
- **Effective age** `e_eff = t − t_init − Δ(t)` — subtracts cumulative rewind boost.
- **Rewind / refresh on redundancy detection**:
  `Δ_e(t) = Δ_max · 1/(1 + exp(−t + e_boosted + t_crit))` — sigmoidal habituation that de-incentivizes spamming the same memory.
- **Substance filter** (LLM-based): keep only if content involves (1) medical condition, (2) clinical status, (3) personality. Reject routine chatter.
- **Redundancy score**: `RS(n,m) = α_NMI · MI(e_n_s, e_m_s) + (1−α_NMI) · JS(e_n_k, e_m_k)`. Defaults: `α_NMI = 0.6`, `RS_min = 0.25`. Triggers pair-and-prune logic in Algorithm 1.
- **Pruning score**: `PS(n) = max_{m ∈ N(n)} (e_nm · τ(e_eff))` — node's best-supported edge; pruned when graph approaches memory cap.
- **Core summary** (always-in-prompt distillation): k-means cluster the graph, take top-scoring node per cluster + top `k₂` extras. Hybrid score `s_hybrid(n;θ) = θ_conn·s_conn + θ_boost·s_boost + θ_rece·s_rece + θ_ent·s_ent` with defaults `θ_conn=0.3, θ_boost=0.3, θ_rece=0.2, θ_ent=0.2`. Recency uses exponential decay, λ=28 days.
- Hyperparameters: `edge_threshold = 0.5`, `α_key = 0.3`, `α_meta = 0.6`.

### Relevance vs. our 10 open questions

| # | Question | Applies? | Note |
|---|---|---|---|
| 1 | Edge weight reinforcement/decay | **yes — direct fit** | Full functional form given. Reverse-sigmoid decay + sigmoidal boost is more sophisticated than vanilla exponential and explicitly avoids both runaway reinforcement and total forgetting. |
| 2 | Memory compression heuristics | **yes** | Redundancy-driven pairing is functionally a soft-clustering compressor. Maps to "N functionally-identical events → behavior profile." |
| 3 | Long-tail protection | **yes** | Floor `d = 0.05` ensures non-zero recall floor — no memory is ever fully unreachable. Pruning gated by `PS` which respects edge quality, not just age. |
| 4 | Hybrid graph + vector | partial | Has edge weights from similarity; recall is probabilistic walk weighted by decay. Less elegant than MemORAI's PageRank. |
| 5 | Memory layer transitions | no | No intermediate tier — asynchronous summarisation feeds graph and prompt directly. Mild conflict with our 5-layer model. |
| 6 | Identity embedding drift | no | But `t_init` + boost timestamps give a primitive — tracking when a node's "freshness" was last reset is the analogue. |
| 7 | Multi-modal identity confidence | no | Text only. |
| 8 | Cross-camera correlation | no | Redundancy-pairing concept transfers: two camera events with high `RS` → mergeable. |
| 9 | Forgetting curve modeling | **yes — direct fit** | Cites Ebbinghaus, Murre & Dros. Reverse-sigmoid chosen over pure exponential because exponential decays too aggressively in the tail. |
| 10 | Agentic memory dynamics | yes | Explicit baseline comparison against MemGPT, Mem0, Memory-R1, OpenAI memory. |

---

## Paper 3 — Trainable Graph Memory

**Authors:** Xia, Xu, Chai, Fan, Song, Wang, Yin, Lin, Zhang, Wang
**Year:** 2025 (November 11, 2025)
**arXiv:** 2511.07800

### Summary

Proposes a three-layer trainable graph memory for LLM agents: raw queries with trajectories, FSM-canonicalized "transition paths," and "meta-cognition" nodes that distill cross-trajectory strategy principles. Edge weights between layers are **learned via REINFORCE** using a downstream-task reward gap. Retrieved meta-cognitions are prepended to the prompt as strategic guidance. Beats A-Mem, Expel, and direct-trajectory baselines, and helps as a warm-start for downstream GRPO RL training.

### Key techniques

- **Three-layer graph**: Query `𝒬` × Transition Path `𝒯` × Meta-Cognition `ℳ`. Directed edges `E ⊆ (𝒬×𝒯) ∪ (𝒯×ℳ)` — strictly bipartite-stacked.
- **FSM canonicalization**: trajectories → canonical paths over states (CorrectGoalEstablished, KnowledgeUncertainGap, StrategyPlanning, ToolExecution, InformationAnalysis, KnowledgeAligned, DecisionMaking, AssumptionBasedReasoning, WrongAnswer, DiagnosisHub, InternalKnowledgeConflict, Start, End). Hand-designed but domain-agnostic state schema.
- **Relevance score** (retrieval): `ρ(m_k|q_new) = Σ_{q_i, t_j} Sim(q_new, q_i)·w_qt^(i,j)·w_tm^(j,k)`. Softmax → top-k=3 meta-cognitions prepended to prompt.
- **Reward gap utility**: `ΔR_k = R_with(m_k) − R_w/o`.
- **REINFORCE objective**: `ℒ_RL = −𝔼_{m_k ~ p}[ΔR_k · log p(m_k | q_new)]`.
- **Gradient update**: `w ← w − α · ∇_w ℒ`, `∇_w ℒ = −ΔR_k · ∇_w log p(m_k | q_new)`. Positive utility ⇒ strengthen path; negative ⇒ weaken.
- **Cold-start fallback**: when only failures exist, retrieve neighbors by `Sim(q_new, q_i)` and pull speculative meta-cognitions from successful paths.
- **Meta-cognition deduplication**: reinforcing existing principles updates confidence; novel pattern → new node; redundant/low-confidence → discarded.
- **Layer signals (GNN-style propagation)**: `𝐇_𝒯^(1) = σ((A_qt ⊙ W_qt)^⊤ 𝐇_𝒬^(0))`, `𝐇_ℳ^(2) = σ((A_tm ⊙ W_tm)^⊤ 𝐇_𝒯^(1))`.

### Relevance vs. our 10 open questions

| # | Question | Applies? | Note |
|---|---|---|---|
| 1 | Edge weight reinforcement/decay | **yes — direct fit** | REINFORCE-style reward-gap updates are exactly the right primitive for our user-FP/FN feedback loop. No decay component — orthogonal to Mnemosyne. |
| 2 | Memory compression heuristics | partial | Meta-cognition layer is a learned abstractor; deduplication by confidence-reinforce-or-new is a soft-clustering rule. |
| 3 | Long-tail protection | partial | Confidence labels (high/medium/low) + uncertainty acknowledgment provide a softer pruning signal than thresholding. |
| 4 | Hybrid graph + vector retrieval | yes | Vector sim only at `𝒬` layer; structural traversal carries the rest. Maps cleanly to our triage approach. |
| 5 | Memory layer transitions | **yes** | Three distinct abstraction layers with explicit promotion (raw trajectory → canonical path → meta-cognition). Closest analogue to our session→episodic→semantic flow. |
| 6 | Identity embedding drift | no | Not addressed. |
| 7 | Multi-modal identity confidence | no | But the confidence-label idea is transferable to KnownActor recognition. |
| 8 | Cross-camera correlation | no | N/A. |
| 9 | Forgetting curve modeling | no | Edge weights learned, not time-decayed. |
| 10 | Agentic memory dynamics | **yes** | Compared against A-Mem and Expel; positions in the RL-augmented memory family. |

---

## Cross-Cutting Synthesis

### Ideas to ADOPT (high-confidence fit)

1. **Reverse-sigmoid decay with non-zero floor (Mnemosyne)** — Adopt for the dispatcher's edge-weight decay function. Replace any plan for pure exponential decay with `τ(e_eff) = (1−d)/(1+e^((e_eff−a)/b))` plus the linear correction near 0.
   - **Why**: pure exponentials decay too fast in the tail and risk losing rare-but-important memories. The non-zero floor `d = 0.05` directly addresses **Open Q3 (long-tail protection)**.
   - **Maps to**: dispatcher's weight-delta computation; episodic edge decay; semantic-rule edge decay.
   - **Starting hyperparams**: `a ≈ 4 weeks` (Mnemosyne's choice for human recall). We should retune for security context — patterns often have weekly cycles.

2. **Sigmoidal rewind/refresh on re-encounter (Mnemosyne)** — Adopt for citation-driven reinforcement. `Δ_e = Δ_max · σ(−t + e_boosted + t_crit)` provides habituation (a memory cited 50 times in an hour shouldn't get 50× the boost a single cite gets).
   - **Why**: directly mitigates runaway reinforcement of dominant patterns.
   - **Maps to**: **Feedback Loop 1** (user FP/FN → edge weights). When VLM cites a memory and user confirms, dispatcher applies `Δ_e` rather than a flat increment.

3. **Dynamic Weighted PageRank for retrieval (MemORAI)** — Adopt as the retrieval primitive for **Category 7 (RAG over episodic)** in our triage. Edge weights conditioned on the current query (cosine similarity in description embeddings) plus PageRank propagation lets us combine structural traversal with vector similarity in a single pass.
   - **Why**: cleanest available answer to **Open Q4 (hybrid graph+vector)**.
   - **Maps to**: triage layer's episodic-retrieval template. Replace plain vector-search-then-hop with PR over a query-weighted subgraph. Seed from k-NN, propagate one hop, top-k=3.
   - **Composition**: MemORAI's edge weights are per-query, not persistent. Compose cleanly with our persistent Hebbian weights: `w_final = w_persistent · w_query`.

4. **Three-tier abstraction with REINFORCE on inter-tier edges (Trainable Graph Memory)** — Adopt the meta-cognition layer as formalization of our **semantic memory layer**.
   - `𝒬` ≈ our episodic events (Visits / closed sessions)
   - `𝒯` ≈ canonical incident paths (FSM over our own state schema: ApproachingDoor → Loitering → Knocking → KnownActorIdentified, etc.)
   - `ℳ` ≈ authored policies + emergent rules
   - Apply REINFORCE updates `ℒ_RL = −𝔼[ΔR_k · log p(m_k | q_new)]` driven by **Feedback Loop 1** — user confirmation (R_with > R_w/o) strengthens; user FP (R_with < R_w/o) weakens.
   - **Maps to**: Feedback Loop 1 directly. Our policy authoring path — emergent policies are the meta-cognition nodes.

5. **Redundancy-driven pair-and-keep-oldest compression (Mnemosyne)** — Adopt as the substrate for episodic→behavior-profile compression. `RS(n,m) = α_NMI·MI + (1−α_NMI)·JS` with `RS_min = 0.25`. When two episodic events score above threshold: keep the older one (anchor), pair the newer, boost the connecting edge.
   - **Why**: compression with provenance — never lose count or recency, but don't blow up the graph.
   - **Maps to**: **Open Q2 (compression heuristics)**. Episodic-layer maintenance task. Pairs match our "behavior profile" concept.

### Ideas to NOTE (relevant but needs deeper evaluation)

6. **FSM-canonicalized transition paths** — Worth prototyping our own state set. Their schema is for reasoning agents; ours would be for physical-world incidents (Approach, Linger, Interaction, Departure, Anomaly). Open question: who authors this FSM, and how brittle is it across edge cases? Test: hand-label 50 incidents from our eval corpus, see if a 10-state FSM covers >90%.

7. **Confidence labels on policy/identity nodes** — Their meta-cognitions carry `high/medium/low` + uncertainty acknowledgment. Apply to **KnownActor** confidence and **emergent-policy** confidence as structured (per-modality face + plate + gait + behavior). Useful for **Open Q7 (multi-modal identity confidence)**.

8. **Core summary asynchronous distillation (Mnemosyne)** — Persistently-in-prompt summary, regenerated periodically via k-means + hybrid scoring (`θ_conn=0.3, θ_boost=0.3, θ_rece=0.2, θ_ent=0.2`). Roughly our `SituationalContext` semantic layer. Evaluate scheduled vs event-driven recomputation.

9. **Substance filter pre-storage (Mnemosyne)** — They drop "non-substantial" turns at intake. We could do this at **VLM-citation time** — VLM tags whether an observation is worth committing to episodic. Tradeoff: cheap but VLM-dependent. Test on eval corpus first.

10. **Cold-start neighbor-borrow (Trainable Graph Memory)** — When a new KnownActor has no history, borrow successful policies from semantically similar actors. Applies to identity layer when a new person/vehicle/pet is first recognized.

### Ideas to SKIP

11. **Persistent in-memory + Redis split (Mnemosyne)** — Edge-device artifact; we've committed to Neo4j 5.x as single store.

12. **Healthcare-specific substance categories (Mnemosyne)** — Drop the category list; keep only the filter pattern.

13. **GRPO/RL training loop integration (Trainable Graph Memory)** — Adopt REINFORCE update on graph weights, but skip the part where meta-cognitions are used as warm-start for downstream RL fine-tuning of the policy model. Our VLM is read-only — we don't fine-tune it. The graph weights are the learnable parameters; the VLM stays fixed. **Explicit divergence** from the paper.

14. **MemORAI's stateless query-conditioned weights only** — Don't adopt as the sole edge-weight model. Their weights are recomputed per query and have no temporal evolution. Solution above: compose `w_persistent · w_query`.

### New open questions raised by the papers

- **Q11**: Should our edge-weight schema separate `w_persistent` (Hebbian, slow-evolving) from `w_query` (recomputed per call)? Composition is unexplored.
- **Q12**: What's our FSM state schema for incident canonicalization, and where does it live (semantic layer, or hard-coded in the dispatcher)? Tradeoff: hard-coded gives reliability at the cost of brittleness; learned gives flexibility at the cost of opacity.
- **Q13**: How does the rewind/refresh sigmoid interact with multiple feedback loops? Mnemosyne has one boost source (re-encounter). We have at least two (citation-driven, user-confirmation-driven). Shared `e_boosted` and `t_crit`, or each gets its own?
- **Q14**: Can the substance filter run on-device before episodic commit, or does it require VLM? Compute-budget question.
- **Q15**: For identity drift (Open Q6), can we use Mnemosyne's redundancy-pair-and-keep-oldest as a drift detector? When a new face embedding pairs with high `RS` to a KnownActor but the embedding distance has slowly grown over time → flag drift, optionally update centroid.

### Citations from the papers worth following

- **Ebbinghaus (1913)** + **Murre & Dros (2015) "Replication and Analysis of Ebbinghaus' Forgetting Curve"** — primary references for Mnemosyne's decay-curve choice. Worth fitting our own decay parameters against the modern Murre/Dros data.
- **Mayo & Crockett (1964)** — primacy-recency effect; Mnemosyne's inspiration for non-uniform temporal weighting.
- **Memory-R1** — beat Mnemosyne on LoCoMo overall (62.74% vs 54.55%); RL-based forgetting with temporal encoding. Sits between Mnemosyne and Trainable Graph in design space.
- **A-MEM** — baseline in Trainable Graph paper; dynamic memory notes that evolve with new inputs. Closest published system to our Hebbian-edge model.
- **Expel** — reusable-trajectory baseline. Adjacent to our session-replay needs.
- **Mem0** — rules-based cache with contradiction-detection forgetting. May inform policy-conflict resolution.
- **HippoRAG / GraphRAG** — canonical hybrid-graph-vector systems for comparison.
- **Contriever** (Izacard et al.) — embedding model MemORAI uses. Candidate for our text-side embeddings.

### Quick alignment summary

- **Most actionable paper for us**: **Mnemosyne**. Direct mathematical fits for decay (Q1, Q9), compression (Q2), long-tail protection (Q3).
- **Most architecturally aligned**: **Trainable Graph Memory**. Three-layer abstraction maps to our memory layers; REINFORCE update is the right primitive for Feedback Loop 1.
- **Most retrieval-relevant**: **MemORAI**. DW-PageRank answers Q4 cleanly, composes with persistent edges.
- **None** of the three papers addresses **identity drift (Q6)**, **multi-modal identity confidence (Q7)**, or **cross-camera correlation (Q8)** — these remain open and likely require literature outside the agentic-memory subfield (computer-vision face-recognition drift literature, multi-camera tracking work).
