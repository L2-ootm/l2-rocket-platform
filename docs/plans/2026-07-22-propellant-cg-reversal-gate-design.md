# Propellant-Driven CG Reversal Recovery Gate

## Outcome

The next campaign may start only after a bounded Rust/OpenRocket gate demonstrates a physically buildable, rules-legal sustainer that is statically stable during ascent and passively reaches a tail-first braking attitude after burnout. The mechanism is the real removal of ascent propellant mass; no commanded fin/motor motion, artificial CG override, arbitrary drag override, or parachute-like device is allowed.

## Why the previous campaign stopped

The attitude-v2 campaign produced ascent-legal vehicles and many tail-first booster descents, but no ascent-legal sustainer recovery. Near the 3 km target, the sustainer sometimes crossed tail-first briefly after apogee and then converged to a fast nose-first equilibrium. More delay optimization cannot repair an absent positive thrust-alignment window.

The audit also found two proxy errors that invalidate further optimization until fixed:

1. OpenRocket bottom-aligns pods while the Rust AST places them at the parent top.
2. Rust stability and dynamic-CG paths collapse multiple motors at different axial stations into one core-mounted motor centroid.

## Considered mechanisms

### A. Propellant-driven CG reversal — selected

Place the three ascent pod motors at an explicit axial station. Their wet propellant keeps ascent CG ahead of CP; consumption moves CG aft without an active command. This is mechanically simple, naturally coupled to the burn, and can be represented identically in OpenRocket and Rust.

### B. Deliberate asymmetry or autorotation

An asymmetric fin/pod layout might create a passive roll/flip path, but the present aerodynamic proxy cannot validate its interference or damping accurately. It also increases asymmetric-flameout risk. Keep as a later branch only after OpenRocket-authority evidence.

### C. Another separable stage

Separation can change both CG and CP, but creates another object that must land below 5 m/s using motor braking. It expands the hardest constraint and is therefore not the first recovery mechanism.

## Geometry contract

- Pod axial placement uses a single top-reference coordinate in both AST and OpenRocket.
- Pod body must have a real structural overlap with the core for at least two pylon stations.
- Pylons attach only inside the overlap of the cylindrical core and pod body; they may not intersect the pod nose.
- Every dimension remains at least 1 mm and total height remains at most 4 m.
- If a pod nozzle ends forward of the core tail, a conservative exhaust-plume clearance check must prove that the aft core and attachments stay outside the plume envelope.
- Structural point masses for pylons use the same stations as emitted OpenRocket geometry.

## Physics contract

- Each active motor retains its own dry mass, propellant mass, axial position, radial offset, and multiplicity.
- Wet, burnout, and post-separation CG are calculated from positioned masses rather than an aggregate curve at the core motor station.
- Static-margin gates evaluate exposed stages at ignition and burnout using the positioned motor set.
- Six-DOF dynamic CG uses the instantaneous propellant centroid of all active motors.
- Existing radial offsets continue to contribute to three-dimensional CG, inertia, and thrust torque.

## Bounded proof gate

Before a long campaign, evaluate a small topology-diverse population and require all of the following:

1. OpenRocket accepts and reopens the generated file with the intended 3+1 topology.
2. All physical/rule checks pass, including attachment and plume clearance.
3. Static margin is at least 1.5 calibers throughout powered ascent and Mach remains below 0.95.
4. Sustainer post-burn telemetry shows a passive transition to tail-first before retro ignition and retains a positive tail-first thrust-alignment window.
5. Both separated stages use only motor braking and touch down below 5 m/s.

## 2026-07-22 Decision: Delayed-Separation Branch Rejected

A bounded diagnostic tested retaining the complete lower 3+1 stage through
the sustainer burn.  Although trimming mid-stack fins raised full-stack
margin from 0.07 to 1.16 calibers, the mechanism requires separation at or
after apogee.  That violates audited OSIFOG hard rule R-002 (stage separation
must precede apogee).  All `osifog_reversal_delayed_*` artifacts are therefore
quarantined and may not seed evolution.  Recovery gates now test the event
ordering directly; post-apogee separation fails closed.
6. Rust candidates are re-ranked only after OpenRocket authority validation; Rust score alone cannot unlock the campaign.

If no candidate passes, the supervisor stops with a classified topology blocker. It must not mutate delays indefinitely or relaunch the same topology.

## Campaign policy after the gate

Use topology epochs: coarse topology/placement search, sensitivity attribution, focused geometry and motor search, then timing polish. Monitor feasibility rates, novelty, score improvement, Rust/OpenRocket disagreement, numerical failures, and population diversity. Stagnation triggers a phase change or a topology stop—not an automatic repeat. Checkpoints and authority records remain append-only and resumable so reruns are idempotent.
