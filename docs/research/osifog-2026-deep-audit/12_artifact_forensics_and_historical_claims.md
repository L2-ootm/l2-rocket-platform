# Artifact Forensics and Historical Claims

## Quarantined Artifacts

### osifog_physical_839k_falcon.ork
- **SHA-256**: 7118214A6DFF2B06C164B02D0574786E133601B1502CED9F24532F20FB86EB38
- **Score**: 839,696.05 (saved in file)
- **Classification**: QUARANTINED
- **Reason**: Stage separation at 39.000 s occurs after first apogee at 23.250 s
- **Legal violations**: Genuine staging gate fails (separation ≥ apogee)
- **Saved simulations**: 1
- **Saved branches**: 2 (Sustainer, Booster)
- **Anti-tumble**: Present and enabled
- **Passive recovery**: None
- **Apogee**: 3000.031 m
- **Max Mach**: 0.943
- **Sustainer touchdown**: 2.648 m/s at E +58.164 / N +109.440 m
- **Booster touchdown**: 2.459 m/s at E +70.282 / N +52.926 m
- **Consumed propellant**: 4.725 kg

**Assessment**: The scoring numbers are internally consistent but the staging violation makes this artifact legally invalid. The physical geometry was previously verified as collision-free (before Gate 2 regression tests).

### osifog_850k_falcon.ork
- **Classification**: QUARANTINED
- **Reason**: Aft ballast disk intersects booster motor mounts
- **Score**: Unknown (never properly validated)
- **Legal violations**: Physical impossibility (intersecting solids)

### osifog_physical_839k_falcon - Copia.ork
- **Classification**: QUARANTINED
- **Reason**: Duplicate of quarantined lineage; not authority-audited

## Diagnostic-Only Artifacts

### falcon_best.ork
- **Classification**: DIAGNOSTIC ONLY
- **Reason**: Superseded by newer candidates; no saved/reopened proof

### osifog_genuine_supported.ork
- **Classification**: DIAGNOSTIC ONLY
- **Reason**: No saved/reopened winning proof

### osifog_supported_candidate.ork
- **Classification**: DIAGNOSTIC ONLY
- **Reason**: No saved/reopened winning proof

## Autonomous Hour Artifacts

### designs/osifog_autonomous_hour/*
- **Classification**: DIAGNOSTIC ONLY
- **Contents**: 7 full Rust → medium proxy → OpenRocket cycles
- **Best result**: 2954.94 m apogee, Mach 0.9186, 1.506 cal margin
- **Landing speeds**: 21.25 and 50.56 m/s (both ILLEGAL)
- **Assessment**: The best cycle achieved marginal stability but could not achieve legal landings

## Historical Score Claims

### "850k achieved"
- **Status**: NOT PROVEN
- **Evidence**: No saved/reopened artifact exists with this score
- **Risk**: May have been from live telemetry or unsaved simulation

### "839,696.05 verified"
- **Status**: VERIFIED but QUARANTINED
- **Evidence**: Saved in .ork file, score reconstructible from saved data
- **Risk**: Staging violation makes it legally invalid

### "3.5135 m/s booster landing"
- **Status**: VERIFIED (branch result only)
- **Evidence**: OpenRocket simulation with H180W motor at 33.104s delay
- **Risk**: This is a single branch result, not a complete vehicle

## Score Reconstruction

For the quarantined 839k artifact:
```
base_score: 900,000.00
- apogee_alt_pen:    3,000 × (3000.031 - 3000)² = 2.883
- apogee_horiz_pen:  16 × ((-2.483)² + (2.154)²) = 172.880
- touch_pos_pen:     2 × (64.223² + 81.183²) = 21,430.546
- touch_vel_pen:     500 × (2.553)² = 3,260.136
- prop_pen:          7,500 × 4.725 = 35,437.500
= 839,696.054
```

The touchdown position loss (21.4k) is the dominant penalty. Even with zero-speed landings and perfect apogee, the score would be ~878k. Crossing 850k requires reducing mean touchdown displacement below ~45 m while keeping all other terms near optimal.
