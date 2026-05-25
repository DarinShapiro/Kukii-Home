# 18 — Hardware Sizing

**Purpose:** Right-size the local hardware fleet given workload model, model choices, and budget constraints. Scale from 1-camera starter to 8+ camera premium setup.
**Status:** preliminary

---

## ⚠ Preliminary estimates pending real-world validation

The numbers in this document are **engineering estimates**, not validated measurements. They will be refined as SentiHome runs in real households over the coming months. Expect significant revisions to:

- Cameras-per-hardware-tier ratios
- Power and thermal envelopes
- Storage growth rates
- Network bandwidth requirements
- Cost projections

**Mode-dependent variance is significant.** The NVR Adapter Layer (§03.5) introduces three operating modes — service, built-in, native — with substantially different resource profiles:

```
Same hardware (Tier 2 NUC + RTX 4060 Ti):
  ├─ Service mode:    handles ~6–8 cameras before saturation
  ├─ Built-in mode:   handles ~10–14 cameras (Frigate offloads preprocessing)
  └─ Native mode:     handles ~12–16 cameras (in-process, single decode)
```

The estimates below **assume service mode** (the v1 universal default). Native and built-in modes will improve camera-per-hardware ratios by 1.5–2.5× once those adapters ship. Until we have measured data from production deployments, treat sizing recommendations as a starting point, not a precise specification.

Real-world deployment data from the maintainer's own household will drive the first revision of this section.

---

## Principle: Local-first, with cloud spillover

- **Baseline:** Fast detector on CPU, VLM on local GPU, everything local-first
- **Headroom:** 30% spare capacity (for bursts, background tasks)
- **Overflow:** Cloud VLM fallback when local GPU saturated
- **Redundancy:** Consider 2-GPU setup for high-availability homes (see below)

---

## Workload model: Expected event rates

### Baseline assumptions

```
Typical residential home:
  - 4 cameras (entry, hallway, backyard, porch)
  - Daytime peak: 100 events/hour (people, delivery, pets)
  - Nighttime: 20 events/hour
  - Daily average: 1500-2000 events

Event breakdown:
  - Detector processes 100%: fast, deterministic (YOLO)
  - Triage deduplicates, scores: 70% deduplicated away
  - VLM processes remaining 30%: confidence reasoning, context

Concrete example:
  100 events/hour → 70 deduplicated away → 30 reach VLM
  30 events/hour × 2-3 sec/inference = 60-90 GPU-seconds/hour = 1.7-2.5% GPU utilization

Burst scenario:
  Peak 1 hour (guests arriving): 200 events → 60 reach VLM
  60 events × 2s = 120 GPU-seconds = 3.3% GPU utilization
  (Still easily handled by modern GPU)
```

### Scaling curves

```
Total workload (events reaching VLM per hour):

1 camera:    8-12 events/hour    (low utilization: <1% GPU)
2 cameras:   16-24 events/hour   (low utilization: 1-2% GPU)
4 cameras:   32-60 events/hour   (low utilization: 2-4% GPU)
6 cameras:   48-90 events/hour   (medium: 4-6% GPU)
8 cameras:   64-120 events/hour  (medium: 6-10% GPU)
12 cameras:  96-180 events/hour  (high: 10-15% GPU)
16 cameras:  128-240 events/hour (saturated: 15-25% GPU)

Breakpoints:
  <5% GPU utilization → single modest GPU sufficient
  5-15% GPU utilization → single strong GPU, or cloud fallback
  15-30% GPU utilization → split load (local + cloud, or 2 GPUs)
  >30% GPU utilization → dedicated GPU per role, or enterprise setup
```

---

## Hardware roles & requirements

Each role has specific compute/memory/storage needs. May run on same host or separate machines.

### Role 1: DVR (RTSP ingest, frame archive)

**Purpose:** Buffer incoming RTSP streams, provide frame archive for clips and replays.

**Hardware profile:**

```
CPU:      2-4 cores @ 2.5+ GHz (transcoding, RTSP demux)
RAM:      4-8 GB (frame buffers, in-memory queue)
Storage:  Depends on retention (see below)
Network:  Gigabit local (camera streams)
GPU:      Not needed (pure CPU workload)

Typical: Intel N100 mini-PC ($150-250) or Raspberry Pi 4 ($100)
```

**Storage sizing for DVR:**

```
Raw frame storage:
  1 camera @ 1080p @ 10fps = 3 Mbps per camera
  4 cameras = 12 Mbps = ~5 TB/month (uncompressed)

H.264 compressed (typical):
  4 cameras = 500 Mbps = ~15 GB/day = 450 GB/month = 5.4 TB/year

With rolling 30-day retention:
  500 GB local SSD + cold storage on NAS
  Or: NAS with 8TB RAID-5 (plenty of headroom)
  Or: Cloud archive (glacier, cheap long-term)
```

**Note:** DVR can be same host as detector/VLM, but separation recommended for reliability (camera stream loss ≠ inference loss).

---

### Role 2: Detector (YOLO, face, re-ID)

**Purpose:** Real-time object detection on every frame (CPU or light GPU).

**Hardware profile:**

```
CPU:      4-8 cores @ 2.5+ GHz (YOLO is CPU-parallelizable)
RAM:      8-16 GB (frame cache, embedding buffers)
GPU:      Optional (Intel Arc A380 $139, NVIDIA RTX 2060 $250+ used)
           Without GPU: YOLO on CPU is ~30ms per frame (still acceptable)
Storage:  100 GB SSD (detector models + embeddings cache)
Network:  Gigabit (receives frames from DVR)

Typical: Intel i5-12400 + RTX 4060 ($600-800) or CPU-only ($300-400)
```

**Performance profile:**

```
YOLO inference:
  - CPU-only: 25-40ms per 1080p frame (real-time at 5-10 fps)
  - With GPU (RTX 4060): 5-8ms per frame (real-time at 120 fps, overkill)

Face detection + embedding:
  - CPU: 15-30ms per face
  - GPU: 3-5ms per face

Re-ID embedding:
  - CPU: 20-40ms per person
  - GPU: 5-8ms per person
```

**Note:** Detector can run on same GPU as VLM (shared VRAM), or separate GPU if high throughput needed.

---

### Role 3: VLM inference (reasoning on frames)

**Purpose:** Visual language model for high-confidence reasoning (Qwen2.5-VL, InternVL, etc.).

**Hardware profile:**

```
Typical VLM:
  - Model size: 2B-7B parameters
  - VRAM needed: 6-14 GB (for fp16 inference)
  - CPU fallback: possible but very slow (5-10s per request)

Recommended GPUs:
  - NVIDIA RTX 3080 (10GB VRAM): runs 7B model, $450-600 used
  - NVIDIA RTX 4060 Ti (16GB VRAM): runs any 7B model, $600-800
  - NVIDIA RTX 4080 (16GB VRAM): runs 13B model, $1200+
  - AMD Radeon RX 7900 XT (24GB VRAM): very competitive, $700-900
  - Intel Arc A770 (16GB VRAM): emerging option, $300-400
  - Apple Neural Engine (M1/M2 Pro/Max): if building on Mac Mini (16GB unified RAM)

Inference latency by GPU:
  Model: Qwen2.5-VL (4B parameters)
    - RTX 3080: ~1.5s per image (10 images/min = 10% utilization)
    - RTX 4060 Ti: ~2s per image (30 images/min = optimal)
    - RTX 4080: ~0.8s per image (75 images/min)

  Larger model: InternVL (26B parameters)
    - RTX 4080: ~3s per image (20 images/min = 7% utilization)
    - RTX 3080: Cannot fit (OOM)
```

**Note:** VLM is the main bottleneck; all hardware decisions revolve around this.

---

### Role 4: Database & vector store

**Purpose:** SQL (sessions, rules, logs) + vector embeddings (faces, re-ID).

**Hardware profile:**

```
Storage:
  - SQL database: ~1-2 GB per 1M sessions
  - Vector DB (FAISS/Qdrant/Milvus): ~500 MB per 1M embeddings
  - For typical home: 10-50 GB sufficient

CPU:
  - Query latency requirement: <200ms for rule matching
  - CPU for indexing: background batch job, low priority

RAM:
  - In-memory cache for hot vectors: 1-2 GB

Typical: Same host as detector/VLM, or small NAS ($300-600 for Synology)
```

---

### Role 5: Event bus (NATS JetStream)

**Purpose:** Async message queue for event routing, durability, subscriber management.

**Hardware profile:**

```
CPU:      2-4 cores (mostly I/O, low CPU)
RAM:      4-8 GB (message queue buffer, subscriptions)
Storage:  1-5 GB NVMe (JetStream persistence, append-only)
Network:  Gigabit local

Typical: Runs on same host as database, or mini-PC ($150-250)
```

---

### Role 6: Home Assistant (if using HA integration)

**Purpose:** Ecosystem integration (device state, automations, TTS, siren).

**Hardware profile:**

```
Compute:
  - Light (pure MQTT relay): 2 cores, 2 GB RAM, Raspberry Pi 4 ($100)
  - Medium (addons: Z-Wave, Thread, Matter): 4 cores, 4 GB RAM, Pi 4 or NUC ($300-400)
  - Heavy (many automations, UI): 8 cores, 8 GB RAM, NUC or laptop ($600+)

Storage:
  - HA database: 100 MB - 1 GB (depends on history length)
  - Addons + integrations: 2-5 GB

Network:
  - Low bandwidth (state queries < 1 Mbps)

Typical: Raspberry Pi 4 with HA OS ($100-150) or existing machine
```

---

## Topologies: Single-box vs split-host

### Topology 1: Single-box (all-in-one)

**Hardware (example: Intel NUC i7):**

```
- Intel NUC 12 i7: 12 cores, 32 GB RAM, 1 TB SSD, ~$800
- Add RTX 4060 Ti GPU: +$650
- Total: $1450

Runs on one machine:
  ✓ DVR (RTSP ingest, frame buffer)
  ✓ Detector (YOLO on CPU/GPU)
  ✓ VLM (inference)
  ✓ Database (SQL + vector)
  ✓ Event bus (NATS)
  ✓ Reasoner (rules, context assembly)
  ✓ Action dispatcher
  ✓ HA integration (optional, add HA on same box)

Advantages:
  + Simplest setup (one box, one IP, one power cord)
  + Low latency (all local network)
  + Cheap ($1500 for decent setup)
  + Easier to debug (everything on same host)

Tradeoffs:
  - Single point of failure (if box dies, everything stops)
  - Resource contention (detector CPU vs VLM GPU)
  - Thermal stress (GPU + CPU in same box under load)
  - Limited scalability (hard to add second GPU later)
```

### Topology 2: Split-host (separated roles)

**Hardware (example: 2 machines):**

```
Machine 1 (Compute): NUC i7 + RTX 4060 Ti ($1450)
  ✓ VLM inference (primary workload)
  ✓ Detector (secondary workload)
  ✓ Database (hot data)
  ✓ Event bus

Machine 2 (DVR/Ingest): Raspberry Pi 4 or N100 ($150-250)
  ✓ DVR (RTSP ingest, frame archive)
  ✓ HA integration (optional)

Total cost: ~$1650

Advantages:
  + Separated workloads (detector CPU, VLM GPU, DVR I/O all isolated)
  + Better thermal management
  + Easier to upgrade (swap machine 1 GPU independently)
  + Resilience (camera loss ≠ inference loss)

Tradeoffs:
  - More complex networking (two IPs, two power supplies)
  - Slightly higher latency (network round-trips)
  - More moving parts to debug
```

### Topology 3: Enterprise (dedicated per role)

**Hardware (example: 4 machines):**

```
Machine 1 (GPU): RTX 4080 NUC ($1800)
  ✓ VLM inference only

Machine 2 (Detector): Intel i5 with RTX 4060 ($600)
  ✓ Fast detector (YOLO, face, re-ID)

Machine 3 (DVR+Storage): Synology NAS ($600)
  ✓ RTSP ingest + archive
  ✓ NVMe SSD (fast frame access for replays)
  ✓ Hard drives (cold archive)

Machine 4 (DB+Bus): Raspberry Pi 4 ($150)
  ✓ PostgreSQL + TimescaleDB
  ✓ Vector DB (Qdrant)
  ✓ NATS JetStream
  ✓ Rules + reasoning

Total cost: ~$3200

Advantages:
  + Maximum reliability (any one failure doesn't stop system)
  + Max performance (no resource contention)
  + Easiest to scale (add more GPU machines in parallel)

Tradeoffs:
  - Expensive
  - Complex setup and debugging
  - Overkill for most homes (4 cameras don't need enterprise gear)
```

---

## Budget tiers: Recommended configurations

### Tier 1: Starter ($800-1200)

**For:** 1-2 cameras, casual users, tech enthusiasts

**Hardware:**

```
Single-box:
  - Intel N100 mini-PC: $250
  - RTX 4060 (8GB VRAM): $350
  - 512 GB SSD: $40
  - 16 GB RAM: $50
  - Power supply, case, peripherals: $100
  Total: $790

Or:
  - Used HP Mini (i5-8250U) + NVIDIA GTX 1050 Ti: $400-500
  - Upgrade RAM to 16GB: $50
  - Add SSD: $40
  Total: $490-590 (if sourced secondhand)
```

**Capability:**

```
✓ 1-2 cameras @ 10fps
✓ Real-time detector (CPU or light GPU)
✓ VLM reasoning (4B model, ~2s inference)
✓ 7-day rolling retention (50GB archive)
✓ Basic rules + alerts
✗ No multi-camera fusion (geometry calibration)
✗ Limited cloud fallback (no redundancy)
✗ No 24/7 availability (single point of failure)
```

**Scaling path:** Add second GPU machine when reaching 4 cameras or budget increases.

---

### Tier 2: Comfortable ($1500-2200)

**For:** 3-4 cameras, security-conscious users, homes with multi-zone coverage

**Hardware Option A (Single-box premium):**

```
  - Intel NUC i7-12700H: $600
  - RTX 4060 Ti (16GB VRAM): $650
  - 32 GB RAM: $100
  - 1 TB NVMe SSD: $80
  - External 2TB HDD (cold archive): $100
  Total: $1530
```

**Hardware Option B (Split-host):**

```
  - NUC i7 + RTX 4060 Ti (compute): $1450
  - Raspberry Pi 4 (DVR): $200
  - 1TB external SSD (archive): $80
  Total: $1730
```

**Capability:**

```
✓ 3-4 cameras @ 15-20fps (good quality)
✓ Fast detector on GPU (8-10ms per frame)
✓ VLM reasoning (7B model, 1.5-2s inference)
✓ 30-day rolling retention (200-300GB local)
✓ Multi-camera identity fusion (if 2+ overlapping cameras)
✓ Feedback-driven optimization (can run silently)
✓ Cloud VLM fallback (GPU saturation relief)
~ Moderate availability (single GPU is SPOF)
```

**Scaling path:** Add second GPU for high-availability setup.

---

### Tier 3: Premium ($2500-4000+)

**For:** 5-8+ cameras, homes with security-critical zones, high uptime requirement

**Hardware (Enterprise split):**

```
  - Compute: RTX 4080 NUC or workstation: $1800-2200
  - Detector GPU: RTX 4060 separate NUC: $600
  - DVR: Synology NAS 4-bay: $600
  - Database: Raspberry Pi or spare laptop: $150-300
  - Network: Gigabit switch, PoE injectors: $200
  Total: $3550
```

**Or:**

```
  - Compute: 2x RTX 4060 Ti in single workstation: $1600
  - DVR: Synology NAS: $600
  - Database: NAS does also: (included above)
  - Network: $200
  Total: $2400 (if dual-GPU in one box)
```

**Capability:**

```
✓ 6-8 cameras @ high FPS
✓ Dual-GPU setup (split VLM and detector)
✓ 30-day local retention + cloud cold archive
✓ Multi-camera stereo calibration (geometry-aware)
✓ Behavioral biometrics (gait, cross-camera)
✓ Real-time optimization feedback
✓ Planned redundancy (GPU failure = fallback to cloud)
✓ 24/7 availability (RAID storage, redundant compute)
✓ Seasonal learning (year of data archived)
```

**Scaling path:** Add cloud integration for additional redundancy, or expand to 12+ cameras with additional GPU machine.

---

## GPU selection guide

### Performance comparison (VLM inference time)

```
Testing: Qwen2.5-VL (4B model) + InternVL (26B model) on 1080p frames

GPU                    VRAM    Qwen2.5-VL (4B)   InternVL (26B)
─────────────────────────────────────────────────────────────
RTX 2060 (6GB)         6GB     3.5s              OOM
RTX 3060 (12GB)        12GB    2.2s              OOM
RTX 3080 (10GB)        10GB    1.5s              OOM
RTX 4060 (8GB)         8GB     2.1s              OOM
RTX 4060 Ti (16GB)     16GB    1.8s              3.2s
RTX 4070 (12GB)        12GB    1.4s              OOM
RTX 4080 (16GB)        16GB    0.8s              3.0s
RTX 4090 (24GB)        24GB    0.5s              2.0s
─────────────────────────────────────────────────────────────
AMD RX 7900 XT (24GB)  24GB    1.2s              2.8s
AMD RX 7900 GRE (12GB) 12GB    1.6s              OOM

Apple M1 Max (unified) 32GB    2.5s              ~8s*
Apple M3 Max (unified) 36GB    2.0s              ~6s*

*Apple uses unified memory (slower than discrete VRAM)
```

**Recommendation:**

```
1-2 cameras:    RTX 4060 (8GB) or Apple M1 Mac Mini
3-4 cameras:    RTX 4060 Ti (16GB) or AMD RX 7900 XT
5-8 cameras:    RTX 4080 (16GB) + RTX 4060 (separate detector)
8+ cameras:     Dual RTX 4080 or enterprise GPU cluster
```

---

## Network & storage sizing

### Network bandwidth

```
Ingest (cameras → DVR):
  4 cameras × 3 Mbps = 12 Mbps (easily handled by Gigabit)
  Peak: may spike to 20 Mbps (no issue)

Processing (DVR → Detector → VLM):
  Frame readback: 1080p frames @ 10fps = 3 Mbps (negligible)

Cloud fallback (home → cloud VLM):
  Typical: 1-5 frames/min to cloud = 500 Kbps (negligible)
  Burst: 10 frames/min = 1 Mbps (still fine)

Recommendation:
  ✓ Gigabit Ethernet for local (cameras, compute boxes, NAS)
  ✓ Shared home broadband for cloud (fallback only, not primary)
  ✓ Separate Wi-Fi for guests (don't share bandwidth with surveillance)
```

### Storage sizing

```
DVR archive (rolling 30-day retention):
  4 cameras × 12 Mbps × 30 days = 12 MB/s × 2.59M sec = 311 GB

Option 1: Local SSD
  ✗ 1TB SSD: expensive, wears out over time
  ✓ Recommendation: 500GB NVMe (last 7 days), external HDD (cold)

Option 2: NAS (Synology, QNAP, Truenas)
  ✓ 8TB RAID-5 = 24TB raw, plenty of headroom
  ✓ Cost: $600-1000 + HDD
  ✓ Benefit: hot/cold tiering, snapshots, redundancy

Option 3: Cloud cold archive
  ✓ S3 Glacier: $1/month per 100GB (cheap long-term)
  ✓ No local hardware needed
  ✗ Latency to retrieve old clips (hours to days)

Recommendation for tier 2 setup:
  500GB local NVMe (7-day hot) + 2TB external HDD (30-day) = $120 total
```

---

## Scaling decisions: When to upgrade

### When to add a second GPU

**Triggers:**

```
✗ Single GPU > 80% utilization for 2+ consecutive days
✗ VLM queue depth > 10 events
✗ Detector FPS < 2 frames/sec (blocked by GPU)
✓ Adding 4+ more cameras (separate GPU for future)
```

**Action:**

```
Add RTX 4060 Ti ($650):
  - Primary GPU: VLM inference
  - Secondary GPU: detector + background tasks
  - Expected improvement: 3-4x throughput per GPU type
```

### When to add stereo calibration & multi-camera fusion

**Triggers:**

```
✓ Installed 2+ overlapping cameras (e.g., doorbell + side approach)
✓ Identity confidence < 0.70 (single camera limitation)
✓ False positives > 50% on particular rules
```

**Action:**

```
1. Calibrate stereo: use phone AR or landmark PnP (2-3 hours)
   - Benefit: 3D face verification, gait biometric correlation
   - Expected improvement: +0.15 identity confidence

2. Enable multi-camera fusion in rules:
   - Cost: extra preprocessing (5-10% GPU)
   - Benefit: 50-80% reduction in identity-related false positives
```

### When to move to cloud-fallback only

**Triggers:**

```
✗ VLM consistently > 95% utilization
✗ Cannot expand local GPU (physical space, power budget)
✓ Cloud budget allows (cost is acceptable)
```

**Action:**

```
1. Keep fast detector local (CPU, always fast)
2. Send VLM requests to cloud (conditional, cost-aware)
3. Reduce local GPU to detector-only (RTX 4060, $300)
4. Save: $350 on GPU, but add ~$5-10/month cloud cost
```

---

## Example configurations by scenario

### Scenario 1: Apartment, 1 camera (doorbell only)

```
Hardware: Single-box budget
  - Used MacBook Air M1 ($800, already owned)
  - OR: Intel NUC + RTX 4060 ($800)

Compute:
  ✓ Doorbell RTSP ingest
  ✓ Real-time detector
  ✓ VLM reasoning (2-3 sec latency acceptable)
  ✓ Simple rules (delivery, package theft, unknown person)

Storage:
  - 7-day clip retention: 30 GB
  - Internal SSD sufficient

Cost: $0 (if reusing laptop) or $800 (new hardware)
```

### Scenario 2: House, 4 cameras (entry, backyard, interior hallway, garage)

```
Hardware: Tier 2 split-host
  - NUC i7 + RTX 4060 Ti (compute): $1450
  - Raspberry Pi 4 (DVR): $200
  - 2TB external HDD: $100

Compute:
  ✓ 4x 10fps detector (CPU or light GPU)
  ✓ VLM for 30+ events/hour
  ✓ Multi-camera identity fusion (hallway + entry)
  ✓ Feedback-driven optimization
  ✓ Cloud VLM fallback during peak

Storage:
  - 30-day retention: 200 GB
  - HDD + NVMe tiering

Cost: $1750 + installation (1-2 days)

Upgrade path: Add 2nd GPU when adding stereo + 2 more cameras
```

### Scenario 3: Property management, 12 cameras (multi-unit building)

```
Hardware: Tier 3 enterprise
  - Workstation (2x RTX 4080 or 1x A100): $3500
  - Synology NAS 8-bay: $800
  - Gigabit switch + PoE: $300
  - Network cabling: $500

Compute:
  ✓ 12 cameras @ 20fps real-time
  ✓ Parallel VLM inference (2 GPUs = 4x throughput)
  ✓ Multi-tenant isolation (separate vector DBs per unit)
  ✓ Long-term pattern learning (12 months stored)
  ✓ Compliance logging (GDPR, audit trails)

Storage:
  - 30-day retention: 1.2 TB
  - 12-month cold archive: 12 TB
  - RAID-6 redundancy

Cost: $5200 + installation (3-5 days)
```

---

## Thermal & power considerations

### Power consumption

```
Typical single-box (NUC i7 + RTX 4060 Ti):
  - Idle: 30W (CPU) + 20W (GPU) = 50W
  - Full load: 100W (CPU) + 130W (GPU) = 230W
  - Sustained operating: ~80-120W average

Example: Running 24/7 at 100W avg
  - Monthly: 100W × 24h × 30 = 72 kWh
  - Cost: 72 kWh × $0.12/kWh = $8.64/month (US average)

For dual-GPU setup: double the cost, so ~$17/month sustained
```

### Thermal management

```
Single GPU (RTX 4060 Ti):
  - TDP: 130W
  - Requires: 250W PSU minimum
  - Cooling: stock cooler sufficient if airflow good
  - Operating temp: 60-75°C (safe range)

Dual GPU (2x RTX 4060 Ti):
  - TDP: 260W
  - Requires: 650W PSU
  - Cooling: custom solution (case fans, external heatsinks)
  - Operating temp: watch for thermal throttling

NAS storage (Synology, 4-8 bays):
  - Thermal: case fans, passive cooling usually sufficient
  - Monitoring: check temps monthly via SNMP alerts
```

### Placement

```
Recommended:
  ✓ Dedicated shelf in cool room (basement ideal)
  ✓ Ventilation (not in closed cabinet)
  ✓ Away from heat sources (radiators, direct sun)
  ✓ Accessible for troubleshooting

Avoid:
  ✗ Enclosed cabinet (thermal buildup)
  ✗ Next to router (interference)
  ✗ Laundry room (vibration, humidity)
```

---

## Maintenance & upgrade timeline

### Year 1 (Post-deployment)

```
Weekly: Monitor thermals, check alerts
Monthly: Verify backups working, test failover
Quarterly: Update models, review performance metrics
```

### Year 2-3

```
Assess: Is GPU utilization trending up?
  → If yes and >80% sustained: plan GPU upgrade

Assess: Are false positives increasing?
  → If yes: run feedback optimization, consider better models

Assess: Storage fill rate?
  → If >80% full within 6 months: expand or cloud archive
```

### Year 3+

```
GPU aging: Check if newer models (RTX 50xx) offer better value
  - Typically: new gen = +40% perf / same price

SSD degradation: if NVMe close to rated cycles, replace
  - Typical lifespan: 3-5 years

Database growth: Vector DB or SQL starting to slow?
  - Compress old embeddings, archive old sessions

Model updates: better VLM released?
  - Test variant against current rule performance
  - Gradual rollout if FP/FN improve
```

---

## Total cost of ownership (TCO)

```
Tier 2 setup (4 cameras, 3 years):

Hardware:
  Initial: $1750 (NUC + GPU + DVR + HDD)
  Year 2: $200 (HDD replacement)
  Year 3: $150 (accessories, fans, upgrades)
  Subtotal: $2100

Operations:
  Power: $8.64/month × 36 months = $311
  Cloud fallback: $3/month × 36 = $108 (optional)
  Internet: included in home internet
  Subtotal: $419

Support:
  Installation: $500 (if not DIY)
  Troubleshooting: $0 (community support, MCP)

Total TCO: $2100 + $419 + $500 = $3019 (over 3 years)
Monthly: $84/month

Compare to:
  Cloud security service: $40-100/month per camera
  4 cameras × $60/month = $2880/year = $8640 / 3 years
  → SentiHome saves $5600 over 3 years + privacy benefit
```
