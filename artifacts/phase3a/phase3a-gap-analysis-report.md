# L2 — OSIFOG Level 3 Phase 3A Gap Analysis Report
## From Illegal ORK to Legal Physical Vehicle

**Date:** 2026-07-20
**Status:** DIAGNOSTIC — NO LEGAL BRANCH
**Classification:** CURRENT_BOOSTER_CONFIGURATION_3D_INFEASIBLE

---

## Executive Summary

The illegal `osifog_850k_falcon.ork` achieves the correct flight profile (3000.016m apogee) but is structurally invalid. Our current "legal" configuration has the wrong separation timing and wrong aerodynamic topology. The solution is NOT more fin tuning — it is fixing the fundamental separation sequence and building proper structural support.

---

## 1. What the Illegal ORK Does Right

### Flight Profile
- **Apogee:** 3000.016 m (exact target)
- **Max velocity:** 325.748 m/s
- **Max acceleration:** 249.47 m/s²

### Structural Elements Present
- Sustainer: body length=0.70m, radius=0.074m, nose cone length=0.74m (haack)
- Booster: body length=0.75m, radius=0.074m
- Both stages: 3-ring cluster motor mounts (Kraft phenolic)
- Sustainer retro motor mount: single, 0.43m length
- Booster retro motor mount: single, 0.43m length

### What Works
1. The 3+1 octaweb-inspired cluster topology is correct
2. The motor sizing reaches 3000m precisely
3. The retro motor mounts exist

---

## 2. What Makes the Illegal ORK Illegal

### Structural Deficiencies
1. **No centering rings** — motor mounts are not structurally supported
2. **No bulkheads** — no separation plane reinforcement
3. **No ballast** — nose mass is not represented as physical geometry
4. **No fins** — no aerodynamic surfaces visible in the XML
5. **No transition** — stages connect without a physical shoulder/adapter

### Separation Timing Error
- **Illegal ORK:** `separationdelay: 37.5` seconds after burnout
- **Required:** Separation at burnout (~1.7s global time)
- **Problem:** 37.5s delay means separation happens during DESCENT, not during ascent

### Missing Components
- Centering rings for motor mount alignment
- Bulkheads at separation plane
- Nose ballast bulkhead
- Fin attachment hardware
- Stage transition/adapter

---

## 3. What Our Current "Legal" Configuration Gets Wrong

### Separation Timing
- **Current:** `s1_separation_delay: 0.0` (separates at burnout) ✓
- **Illegal ORK:** `separationdelay: 37.5` (separates during descent) ✗

### Aerodynamic Topology
- **Current:** 4 fins on booster, 0.80m body, 0.38m fin height
- **Problem:** Nose angle collapses from 62° to 6° in 6s after apex
- **Root cause:** Fins are too small and CG is too far aft

### Motor Configuration
- **Current:** s1_retro=19 (K550W) — NOT LEGAL for booster retro
- **Legal options:** H180W, J350W, J420R, J350W

---

## 4. The Correct Flight Sequence

```
t=0.0s    Launch (3 motors fire)
t=0.3s    Launch rod clearance
t=1.7s    Booster burnout (3 motors)
t=1.7s    STAGE SEPARATION (immediate)
t=1.7s    Booster: retro motor ignites (braking)
t=1.7s    Sustainer: 3 motors ignite (continuing to 3000m)
t=5.0s    Sustainer burnout
t=8.5s    Booster apex
t=20.7s   Sustainer apex (3000m)
t=31.2s   Booster ground hit (retro-braked)
t=45.4s   Sustainer ground hit (retro-braked)
```

### Critical: Separation MUST Happen at Burnout
- The booster must separate at t=1.7s, NOT t=39.2s
- After separation, the booster is an independent vehicle
- The booster fires its retro motor immediately after separation
- The sustainer ignites its 3 motors immediately after separation

---

## 5. What Needs to Change

### A. Structural Support (Must Add)
1. **Centering rings** — align motor mounts within body tubes
2. **Bulkheads** — reinforce separation plane, provide attachment points
3. **Nose ballast bulkhead** — physical steel ballast at nose position
4. **Fin mounting plates** — structural attachment for fins
5. **Stage transition** — shoulder/adapter between stages

### B. Separation Timing (Must Fix)
- Change booster `separationdelay` from 37.5s to 0.0s
- Ensure separation event is `burnout`
- Verify separation occurs at t=1.7s in simulation

### C. Aerodynamic Topology (Must Redesign)
- 8 fins on booster (proven: reduces horizontal speed 50%)
- Fin height: 0.65-0.80m (proven optimal range)
- Fin sweep: ≤5° (proven: >5° flips nose-first)
- No aft ballast (proven: worsens performance)
- No forward ballast (proven: flips nose-first)

### D. Motor Selection (Must Fix)
- s1_retro: use H180W or J350W (LEGAL options)
- s0_retro: K550W is legal for sustainer
- Verify motor fits within cluster geometry

---

## 6. The Path Forward

### Phase 4A: Structural Legalization
1. Take the illegal ORK's flight profile
2. Add all required structural components
3. Fix separation timing to 0.0s delay
4. Verify ascent authority (Mach < 0.95, margin > 1.5 cal)

### Phase 4B: Aerodynamic Redesign
1. Implement 8-fin booster configuration
2. Optimize fin geometry within proven parameters
3. Verify tail-first descent alignment (q > 0.5)
4. Validate free-descent speed < 15 m/s

### Phase 4C: Powered Landing
1. Select legal retro motor
2. Fire motor during tail-first descent
3. Verify motor doesn't flip rocket nose-first
4. Target: < 5 m/s total touchdown speed

---

## 7. Key Metrics from Current Analysis

| Metric | Illegal ORK | Current Legal | Target |
|--------|-------------|---------------|--------|
| Apogee | 3000.016m | 2312m | 3000m |
| Separation time | 39.2s (WRONG) | 1.7s (CORRECT) | 1.7s |
| Booster free-descent | Unknown | 21.70 m/s | < 5 m/s |
| Horizontal speed | Unknown | 19.04 m/s | < 3 m/s |
| Structural support | None | Validated | Validated |
| Legal motors | Unknown | s1_retro illegal | All legal |

---

## 8. Conclusion

The solution is NOT more topology tuning. The solution is:

1. **Fix the separation sequence** — separate at burnout, not during descent
2. **Build proper structure** — centering rings, bulkheads, ballast
3. **Use the proven 8-fin topology** — reduces horizontal speed 50%
4. **Select legal motors** — H180W or J350W for booster retro
5. **Validate the complete sequence** — both stages land tail-first

The illegal ORK proves the flight profile works. We need to make it physically legal while preserving the correct flight dynamics.

---

**Next Command:** Begin Phase 4A — Structural Legalization of the ORK.
