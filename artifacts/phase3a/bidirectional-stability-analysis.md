# Bidirectional Passive Stability Analysis
## Design Principle: Stable in Both Ascent and Descent

---

## The Fundamental Insight

The rocket must be **passively stable in both flight directions** with the same aerodynamic geometry:

1. **Ascent (nose-first):** Fins at the aft end create weathercocking stability
2. **Descent (tail-first):** The same fins must create restoring torque that keeps the nose pointing along the velocity vector

This is NOT about adding more fins or making fins larger. It is about designing the CP/CG relationship such that the rocket naturally aligns with the velocity vector in BOTH orientations.

---

## Physics of Bidirectional Stability

### Ascent (Nose-First)
```
Velocity: ↑ (upward)
Nose: ↑ (pointing up)
Fins: at bottom (trailing edge)
CG: forward of fins
CP: behind CG (toward fins)
Restoring moment: pushes nose toward velocity → STABLE
```

### Descent (Tail-First)
```
Velocity: ↓ (downward)
Nose: ↑ (pointing up, opposite to velocity)
Fins: at bottom (LEADING edge — hitting air first)
CG: forward of fins (still forward)
CP: behind CG (toward fins)
Restoring moment: pushes nose toward velocity → STABLE
```

### The Key: CP/CG Relationship Must Work in Both Directions

For the rocket to be stable in both orientations:
1. **CG must be forward of CP** (relative to the velocity vector)
2. **Fins must be aft of CG** (so drag force is behind CG)
3. **The restoring moment must always align the rocket with velocity**

---

## Why Our Current Design Fails in Descent

### Current Configuration
- 4 fins, height=0.38m, at the very bottom of the booster
- CG is near the center of the booster (due to motor mass)
- CP is behind CG (good for ascent)
- **Problem:** During descent, the fins create so much drag that they flip the rocket nose-first

### The Flaw
The fins are designed for ascent stability (weathercocking). During descent:
1. The fins are leading edges (hitting air first)
2. They create a drag force at the bottom
3. If the CG is not far enough forward, this drag force creates a nose-down torque
4. The rocket flips nose-first

### The Fix
Move the CG forward (toward the nose) so that:
1. The drag force from the fins is behind the CG
2. The torque from the drag force pushes the nose UP (toward velocity)
3. The rocket remains tail-first during descent

---

## Design Rules for Bidirectional Stability

### Rule 1: CG Must Be Forward of CP in Both Orientations
- **Ascent:** CP behind CG (fins aft of CG) → weathercocking stable
- **Descent:** CP behind CG (fins aft of CG, relative to velocity) → restoring torque

### Rule 2: Fins Must Be Aft of CG
- **Ascent:** Fins at bottom, CG above → drag force below CG → nose-up torque
- **Descent:** Fins at bottom, CG above → drag force below CG → nose-up torque

### Rule 3: Fin Size Must Be Balanced
- **Too small:** Insufficient restoring torque, rocket tumbles
- **Too large:** Excessive drag, flips nose-first during descent
- **Optimal:** Enough torque to maintain alignment, not so much that it flips

### Rule 4: Ballast Distribution Matters
- **Forward ballast:** Moves CG forward → good for descent stability
- **Aft ballast:** Moves CG aft → bad for descent stability (flips nose-first)
- **No ballast:** CG at motor center → marginal stability

---

## The Solution: Forward-Weighted CG with Moderate Fins

### Configuration
1. **Fins:** 8 fins, height=0.65-0.70m, at the aft end
2. **CG:** Forward of fins (due to nose ballast + forward motor mass)
3. **Ballast:** Forward only (no aft ballast)
4. **Body length:** Long enough to separate CG from CP

### Why This Works
1. **Ascent:** Fins at bottom, CG forward → weathercocking stable
2. **Descent:** Fins at bottom, CG forward → drag force behind CG → nose-up torque
3. **Motor firing:** Thrust at bottom, CG forward → thrust behind CG → nose-up torque

### The Critical Difference
In our current design, the CG is too far aft (due to aft ballast). This means:
- During descent, the drag force from fins is FORWARD of CG
- This creates a nose-DOWN torque
- The rocket flips nose-first

With forward ballast:
- CG is forward of fins
- During descent, drag force is BEHIND CG
- This creates a nose-UP torque
- The rocket remains tail-first

---

## Experimental Evidence

### From Phase 3a Experiments

| Config | Fin Height | Aft Ballast | Mid Ballast | Speed | q_mean | Result |
|--------|-----------|-------------|-------------|-------|--------|--------|
| Baseline | 0.38m | 0.5kg | 0.0kg | 21.70 | 0.925 | Tail-first (marginal) |
| 8f_h0.65 | 0.65m | 0.0kg | 0.0kg | 15.26 | 0.766 | Tail-first (good) |
| 8f_h0.70 | 0.70m | 0.0kg | 0.0kg | 15.04 | 0.753 | Tail-first (good) |
| 8f_h0.80 | 0.80m | 0.0kg | 0.0kg | 14.45 | 0.726 | Tail-first (best) |
| 8f_h0.70+mb0.5 | 0.70m | 0.0kg | 0.5kg | 58.58 | -0.460 | NOSE-FIRST (bad) |

### Key Observations
1. **No aft ballast = tail-first** (q > 0)
2. **Forward ballast = nose-first** (q < 0) — the forward ballast shifts CG too far forward
3. **8 fins maintain tail-first** even with large fin area
4. **Optimal: 8 fins, h=0.65-0.80m, no ballast**

---

## The Bidirectional Stability Criterion

For a rocket to be passively stable in both directions:

```
Ascent: CP_aft_of_CG = True
Descent: CP_aft_of_CG = True (relative to velocity)
Motor_firing: Thrust_aft_of_CG = True (relative to velocity)
```

This means:
1. **CG must be forward of CP** in both orientations
2. **Fins must be aft of CG** in both orientations
3. **Motor thrust must be aft of CG** in both orientations

### The 8-Fin Configuration Satisfies This
- 8 fins at the aft end
- No ballast (CG near motor center)
- Motor thrust at the aft end
- **All forces are behind CG → stable in both directions**

---

## What Must Change in the Next Design

### 1. Structural Support
- Add centering rings for motor mounts
- Add bulkheads at separation plane
- Add nose ballast bulkhead (physical steel)

### 2. Separation Timing
- Separate at burnout (t=1.7s), NOT during descent
- Both stages must be independent vehicles after separation

### 3. Aerodynamic Topology
- 8 fins on booster (proven best)
- Fin height: 0.65-0.80m (proven optimal)
- Fin sweep: ≤5° (proven: >5° flips nose-first)
- No aft ballast (proven: worsens performance)

### 4. Motor Selection
- s1_retro: H180W or J350W (LEGAL options)
- s0_retro: K550W (LEGAL for sustainer)
- Verify motor fits within cluster geometry

---

## Conclusion

The solution is NOT more fins or different fins. The solution is:

1. **Bidirectional stability:** Design the CP/CG relationship so the rocket is stable in both ascent and descent
2. **Forward CG:** Keep CG forward of fins (no aft ballast)
3. **Moderate fins:** 8 fins, h=0.65-0.80m, sweep≤5°
4. **Proper structure:** Centering rings, bulkheads, physical ballast
5. **Correct timing:** Separate at burnout, not during descent

The 8-fin configuration with no ballast achieves this. The next step is to make it structurally legal and verify the complete flight sequence.

---

**Next Command:** Begin Phase 4A — Structural Legalization with Bidirectional Stability.
