# Reference design diagnosis + proposed 4-sidepod hybrid architecture

**Written 2026-07-24. This document exists for two reasons: (1) to record a full, evidence-based diagnosis of `designs/osifog_level3/osifog_physical_839k_falcon_almost_legal.ork` so nobody re-derives it from scratch, and (2) to precisely capture a new rocket architecture the user proposed after seeing that diagnosis, for a future engineering pass (human or the user's own "heavy load agent") to evaluate with real aerospace-engineering judgment. Per explicit instruction: this document does not force speculative answers to the open engineering questions in §3 — those are genuinely open, and an invented answer would be worse than admitting they're unresolved.**

## 1. What `osifog_physical_839k_falcon_almost_legal.ork` actually is, verified by real simulation

All numbers below came from actually loading and simulating this file through the real bundled OpenRocket 24.12 JVM (not from parsing XML alone, and not from the file's own name/history) — see the session transcript for the exact scripts used; they're reproducible against `lib/OpenRocket-24.12.jar`.

**Structure**: 2 stages, stacked tandem (`Sustainer` on top, `Booster` below — confirmed via `<stage>` order in the XML). Each stage has a 3-ring main motor cluster plus one "Structural Retro Sleeve" motor:
- Sustainer main cluster: 3× `949J150-P`.
- Sustainer retro: 1× `K550W`.
- Booster main cluster: 3× `J360`.
- Booster retro: 1× `K550W`.

**Ascent performance is genuinely good**: apogee 2982.36m (18m short of the 3000m target — very close), max Mach 0.795 (comfortably legal, supersonic ban is Mach ≥1), static margins 1.91 calibers (M0.3 phase) and 0.60 calibers (M2 phase) — both positive, genuinely stable by any measure.

**Two real, confirmed problems, both about motor timing, not thrust/propellant amount:**

### 1a. Both stages' main motor clusters ignite simultaneously at launch — physically implausible for a tandem stack

From the XML, every motor mount (`Sustainer Main Motor Mount`, `Sustainer Structural Retro Sleeve`, `Booster Main Motor Mount`, `Booster Structural Retro Sleeve`) is configured with `ignitionevent=automatic`, `ignitiondelay=0.0`. From the real simulated event log (both branches, timestamped):

```
Sustainer:  t=0.000  IGNITION  (main cluster, 949J150-P x3)
Booster:    t=0.000  IGNITION  (main cluster, J360 x3)
Sustainer:  t=2.130  BURNOUT, STAGE_SEPARATION
Booster:    t=2.130  BURNOUT, STAGE_SEPARATION
```

Both main clusters ignite together at t=0 and burn out together at t=2.13s. Sustainer is physically stacked *above* Booster. For that to be physically sensible, Sustainer's motor exhaust would need a clear path through the still-attached, still-burning Booster stage below it — which this design does not have (it's a normal solid tandem stack, no hollow fire-through channel). This is the exact concern the user raised independently before this document existed ("I think the peripheral motors ignite at launch time too, so it is physically impossible") — confirmed correct.

### 1b. Retro-ignition timing is disconnected from actual landing, for both stages — real touchdown speeds are 14–27x over the legal limit

```
Sustainer retro (K550W):  ignites t=2.13s, burns out ~t=8.56s  -- 45 SECONDS before actual ground impact at t=53.99s
Booster retro (K550W):    ignites t=65.28s                     -- 43 SECONDS AFTER actual ground impact at t=22.62s
```

Neither stage's retro motor provides any braking at the moment it actually matters. Real simulated touchdown speeds, pulled directly from `FlightDataType.TYPE_VELOCITY_TOTAL` at ground crossing:

- Sustainer: **137.8 m/s** (limit is 5 m/s — 27.6x over)
- Booster: **71.1 m/s** (14.2x over)

This also directly explains the 5 critical "Flight Event occurred after landing" warnings OpenRocket reports on this file (ignition/burn/ejection/separation events scheduled on fixed timers that don't know the vehicle has already hit the ground).

**Conclusion on this file**: the name "almost legal" is misleading. It is legal on geometry, Mach, and stability, but hard-fails the rule the entire competition is actually about (soft retro-landing under 5 m/s) by more than an order of magnitude on both stages, and has a real structural implausibility in how both stages' main motors are scheduled. It is not a "needs more propellant/bigger motors" problem — the ascent already works. It needs correct staging sequencing (Sustainer's cluster should not fire until after Booster separates) and correctly-timed retro ignition per stage (triggered relative to each stage's own actual descent, not a fixed global timer).

## 2. Proposed new architecture (user's idea, recorded verbatim in structure, not yet evaluated)

Prompted by the diagnosis above, the user proposed a different topology, described here as precisely as their description allows:

- **Stage 1 (booster)**: keep the current design as-is (the existing Booster stage from the reference file above — 3-ring main cluster + central retro, on its own shared body tube).
- **Stage 2 (sustainer / "main rocket")**: replace the current design with one that has **4 side pods**, physically attached touching the main body tube ("like real boosters" — i.e., strap-on/parallel-staged pods rigidly mounted to the main tube's exterior, not free-flying, not the current octaweb's shared-single-tube-only convention).
  - **2 of the 4 pods fire at launch time**, alongside stage 1, to help the vehicle reach altitude (a parallel-staged assist during initial ascent).
  - After stage 1 (booster) separates, **the pods' 3 engines** (phrasing as given: "the 3 engines inside fire and reach 3000m" — likely means the remaining active motors, not yet disambiguated, see open questions below) fire to continue ascent to the 3000m target.
  - At touchdown, the heavy (main/sustainer) stage lands using **3 motors: 2 lateral (side pods) + 1 central** — i.e., the landing/retro-braking burn for this stage is split across two of the side pods plus a central motor, not a single central retro motor as in the current octaweb design.

The user explicitly said this can be improved further, and that if implementing it is too much work right now, it should be documented (as done here) for a future, more thorough engineering pass — including their own planned "heavy load agent" — to evaluate "based on real engineering knowledge."

## 3. Open engineering questions — genuinely unresolved, not answered here on purpose

These need real aerospace-engineering judgment, not a guess inserted to make this document feel complete:

- **Which specific motors ("the 3 engines inside") fire during the stage-2 ascent-to-3000m phase, and which 2 pods fire at launch vs. later?** The description names "4 side pods, 2 firing at launch" and separately "the 3 engines inside fire and reach 3000m" — it's not yet clear whether these are the same motors described twice, or whether there's a third motor group (e.g., a central sustainer motor plus the pods) not fully spelled out. Needs clarification or engineering judgment to resolve into an unambiguous motor/ignition schedule.
- **Structural/aerodynamic feasibility of 2 of 4 pods igniting at launch while attached to the main tube, alongside the booster stage below.** Real strap-on boosters (Falcon Heavy, Ariane, Space Shuttle SRBs) do exactly this, so it's not implausible in general — but it needs real thrust-vector/moment-balance analysis (do 2 of 4 pods firing asymmetrically at launch introduce a net torque the vehicle can't correct, given this pipeline explicitly cannot model active guidance per the real OSIFOG rule — see `docs/PROJECT_STATUS.md` §3), not an assumption that symmetry works out.
- **How pod separation/jettison is sequenced relative to the booster stage separation**, and whether the current pipeline's staging/event model (built around simple sequential tandem separation) can represent 3 separation events (booster jettison, then 2-of-4-pod jettison, or however many discrete separation events this implies) without new engineering work.
- **Landing dynamics for a 3-motor (2 lateral + 1 central) simultaneous retro burn** — whether this is meant to be a genuinely 3-motor simultaneous burn (higher total thrust, shorter burn, but real complexity in ensuring all 3 motors produce a net-vertical, non-tumbling deceleration) versus some other split, and whether the current physics/scoring pipeline (built around a single central retro per stage) needs new modeling to represent this correctly at all.
- **Implementation cost**: this is a genuinely different topology class from anything currently supported. The active AST generator (`rocket_ast.py`) only knows how to build a single shared body tube per stage (the octaweb convention) or a single plain motor mount — it has no concept of rigidly-attached external pods at all. A prior session explicitly retired an external-pod ("PodSet 3+1") architecture in favor of the current shared-tube octaweb approach (see `.planning/PODSET-EXTERNAL-3PLUS1-ARCHITECTURE.md`, marked superseded) — this proposal is not identical to that retired design (this one describes pods "touching the main tube like real boosters," a different physical arrangement than what PodSet described), but anyone picking this up should read that superseded document first to understand what was tried before and why it didn't become the active approach, rather than assume this is a clean-slate proposal.

## 4. What this document is NOT

This is not a decision to build the 4-sidepod architecture, and not a verdict that the current octaweb approach should be replaced. It is a precise, evidence-backed record of (a) what's actually wrong with the specific reference file that prompted this conversation, and (b) exactly what the user proposed as a possible next direction, written down accurately enough that a future engineering pass doesn't have to reconstruct the idea from a paraphrase.
