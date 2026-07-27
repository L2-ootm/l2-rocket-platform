"""Gate A.1: Collected tests for Phase 1 findings.

Converts critical Phase 1 script assertions into pytest-collected tests.
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import pytest


# ── Motor Data Canonical Tests ──

class TestMotorDataCanonical:
    """Verify canonical motor_data.py loads correctly from .eng files."""

    def test_j510w_loads_from_eng(self):
        sys.path.insert(0, ".")
        import motor_data
        m = motor_data.load_motor_by_index(16)  # J510W
        assert m.designation == "J510W"
        assert abs(m.propellant_mass_kg - 0.6620) < 1e-4
        assert abs(m.loaded_mass_kg - 1.0800) < 1e-4
        assert abs(m.burn_duration_s - 2.500) < 1e-3

    def test_k550w_loads_from_eng(self):
        sys.path.insert(0, ".")
        import motor_data
        m = motor_data.load_motor_by_index(19)  # K550W
        assert m.designation == "K550W"
        assert abs(m.propellant_mass_kg - 0.9197) < 1e-4
        assert abs(m.burn_duration_s - 3.356) < 1e-3

    def test_dry_mass_equals_loaded_minus_propellant(self):
        sys.path.insert(0, ".")
        import motor_data
        for idx in range(38):
            try:
                m = motor_data.load_motor_by_index(idx)
                assert abs(m.dry_mass_kg - (m.loaded_mass_kg - m.propellant_mass_kg)) < 1e-9
            except (FileNotFoundError, ValueError):
                pass  # Missing .eng files expected

    def test_burn_duration_from_curve_domain(self):
        sys.path.insert(0, ".")
        import motor_data
        m = motor_data.load_motor_by_index(16)  # J510W
        assert m.burn_duration_s == m.time_points_s[-1] - m.time_points_s[0]
        assert m.burn_duration_s > 0

    def test_total_impulse_positive(self):
        sys.path.insert(0, ".")
        import motor_data
        m = motor_data.load_motor_by_index(16)
        assert m.total_impulse_ns > 0


# ── Mass Conservation Tests ──

class TestMassConservation:
    """Verify mass conservation for a simulated vehicle."""

    def test_mass_conservation_basic(self):
        sys.path.insert(0, ".")
        from osifog_sweep import (
            init_or, generate_ork, SIM_SEED, _seed_multilevel_wind,
            _load_ork_doc, parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
        )
        import jpype

        fixture = {
            "s0_main": 16, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
            "main_cluster_count": 3, "s0_body_rad": 0.074, "s1_body_rad": 0.074,
            "s0_body_len": 0.70, "s1_body_len": 0.75,
            "s1_separation_delay": 0.0, "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
            "nose_mass_kg": 1.72, "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
            "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
            "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 2.725,
            "s1_aft_ballast_pos_m": 0.084, "s1_aft_ballast_rod_radius_m": 0.014,
            "s1_aft_ballast_attachment": "central_bonded",
            "s0_fin_count": 4, "s0_fin_root": 0.20, "s0_fin_height": 0.25, "s0_fin_sweep": 10.0,
            "s1_fin_count": 4, "s1_fin_root": 0.24, "s1_fin_height": 0.38, "s1_fin_sweep": 0.05,
            "s1_grid_fin_count": 4, "s1_grid_fin_root": 0.10, "s1_grid_fin_height": 0.08,
            "s1_grid_fin_position_m": 0.03,
            "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
            "s0_grid_fin_position_m": 0.03,
            "s0_fin_thickness_m": 0.003, "s1_fin_thickness_m": 0.003,
            "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.003,
            "s0_fin_material": "fiberglass", "s1_fin_material": "fiberglass",
            "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
            "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
            "wind_levels": parse_wind_csv(WIND_CSV),
        }

        init_or()
        ork_xml = generate_ork(fixture)
        fd, path = tempfile.mkstemp(suffix=".ork")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ork_xml)
            doc = _load_ork_doc(path)
            sim = doc.getSimulations().get(0)
            sim.getOptions().setRandomSeed(SIM_SEED)
            _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
            sim.simulate(_get_anti_tumble_listener())
            data = sim.getSimulatedData()

            fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
            FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

            br0 = data.getBranch(0)
            initial_mass = float(br0.get(fdt.TYPE_MASS)[0])

            landed_masses = []
            for bi in range(int(data.getBranchCount())):
                branch = data.getBranch(bi)
                hit_time = None
                for ev in branch.getEvents():
                    if ev.getType() == FlightEvent.Type.GROUND_HIT:
                        hit_time = float(ev.getTime())
                        break
                if hit_time is None:
                    continue
                times = branch.get(fdt.TYPE_TIME)
                masses = branch.get(fdt.TYPE_MASS)
                n = int(branch.getLength())
                for i in range(1, n):
                    if float(times[i]) >= hit_time:
                        f = (hit_time - float(times[i-1])) / (float(times[i]) - float(times[i-1]))
                        landed_masses.append(float(masses[i-1]) + f * (float(masses[i]) - float(masses[i-1])))
                        break

            total_landed = sum(landed_masses)
            consumed = initial_mass - total_landed

            # Retro motors (K550W) don't burn in free-descent (delay=200s > impact)
            import motor_data
            s0_main_m = motor_data.load_motor_by_index(fixture["s0_main"])
            s1_main_m = motor_data.load_motor_by_index(fixture["s1_main"])
            expected_burned = 3 * s0_main_m.propellant_mass_kg + 3 * s1_main_m.propellant_mass_kg

            assert abs(consumed - expected_burned) / expected_burned < 0.05, (
                f"Mass conservation: consumed={consumed:.4f}, expected={expected_burned:.4f}"
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── Diagnostic/Legality Separation Tests ──

class TestDiagnosticLegalitySeparation:
    """Verify diagnostics survive legality failure."""

    def test_diagnostics_returned_when_illegal(self):
        sys.path.insert(0, ".")
        from osifog_sweep import (
            init_or, generate_ork, SIM_SEED, _seed_multilevel_wind,
            _load_ork_doc, parse_wind_csv, WIND_CSV, _get_anti_tumble_listener,
            validate_hard_constraints,
        )
        import jpype

        # Use a supersonic candidate (J510W×3 with small body)
        fixture = {
            "s0_main": 16, "s1_main": 18, "s0_retro": 19, "s1_retro": 19,
            "main_cluster_count": 3, "s0_body_rad": 0.074, "s1_body_rad": 0.074,
            "s0_body_len": 0.70, "s1_body_len": 0.75,
            "s1_separation_delay": 0.0, "s0_retro_delay": 200.0, "s1_retro_delay": 200.0,
            "nose_mass_kg": 0.1, "nose_ballast_pos_m": 0.45, "nose_length_m": 0.50,
            "s0_mid_ballast_kg": 0.0, "s1_mid_ballast_kg": 0.0,
            "s0_aft_ballast_kg": 0.0, "s1_aft_ballast_kg": 0.0,
            "s0_fin_count": 3, "s0_fin_root": 0.05, "s0_fin_height": 0.08, "s0_fin_sweep": 10.0,
            "s1_fin_count": 3, "s1_fin_root": 0.05, "s1_fin_height": 0.08, "s1_fin_sweep": 10.0,
            "s1_grid_fin_count": 0, "s1_grid_fin_root": 0.06, "s1_grid_fin_height": 0.06,
            "s1_grid_fin_position_m": 0.03,
            "s0_grid_fin_count": 0, "s0_grid_fin_root": 0.06, "s0_grid_fin_height": 0.06,
            "s0_grid_fin_position_m": 0.03,
            "s0_fin_thickness_m": 0.001, "s1_fin_thickness_m": 0.001,
            "s0_grid_fin_thickness_m": 0.001, "s1_grid_fin_thickness_m": 0.001,
            "s0_fin_material": "legal_balsa", "s1_fin_material": "legal_balsa",
            "s0_grid_fin_material": "fiberglass", "s1_grid_fin_material": "fiberglass",
            "launch_azimuth": 34.0, "launch_angle_deg": 3.85,
            "wind_levels": parse_wind_csv(WIND_CSV),
        }

        init_or()
        ork_xml = generate_ork(fixture)
        fd, path = tempfile.mkstemp(suffix=".ork")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(ork_xml)
            doc = _load_ork_doc(path)
            sim = doc.getSimulations().get(0)
            sim.getOptions().setRandomSeed(SIM_SEED)
            _seed_multilevel_wind(sim.getOptions(), SIM_SEED)
            sim.simulate(_get_anti_tumble_listener())
            data = sim.getSimulatedData()

            fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
            br0 = data.getBranch(0)
            n0 = int(br0.getLength())
            alt0 = br0.get(fdt.TYPE_ALTITUDE)
            stab0 = br0.get(fdt.TYPE_STABILITY)
            vz0 = br0.get(fdt.TYPE_VELOCITY_Z)

            apex_idx = max(range(n0), key=lambda i: float(alt0[i]))
            ascent_stability = [
                float(stab0[i])
                for i in range(apex_idx + 1)
                if float(vz0[i]) > 0.01 and math.isfinite(float(stab0[i]))
            ]
            mach = float(data.getMaxMachNumber())
            min_margin = min(ascent_stability) if ascent_stability else float("-inf")

            legal, violations = validate_hard_constraints(
                {"mach": mach, "min_static_margin": min_margin, "status": "SIMULATED",
                 "stage_landings": [], "event_times": {}, "branch_event_times": []},
                fixture,
            )

            # Diagnostics should be present even when illegal
            assert mach is not None
            assert math.isfinite(min_margin)
            assert not legal  # Should be illegal (supersonic + unstable)
            assert len(violations) > 0
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── Anti-Tumble Real Event Tests ──

class TestAntiTumbleRealEvent:
    """Verify anti-tumble listener intercepts real TUMBLE events."""

    def test_booster_tumble_intercepted(self):
        sys.path.insert(0, ".")
        from osifog_sweep import (
            init_or, _load_ork_doc, SIM_SEED, _seed_multilevel_wind,
            _get_anti_tumble_listener,
        )
        import jpype

        ork_path = "designs/osifog_autonomous_hour/gate4-sustainer-search/anti-tumble-verification.ork"
        if not Path(ork_path).exists():
            pytest.skip("anti-tumble-verification.ork not found")

        init_or()
        doc = _load_ork_doc(ork_path)
        sim = doc.getSimulations().get(0)
        sim.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim.getOptions(), SIM_SEED)

        # Without listener
        sim.simulate()
        data_off = sim.getSimulatedData()
        fdt = jpype.JClass("info.openrocket.core.simulation.FlightDataType")
        FlightEvent = jpype.JClass("info.openrocket.core.simulation.FlightEvent")

        tumble_off = None
        for bi in range(int(data_off.getBranchCount())):
            for ev in data_off.getBranch(bi).getEvents():
                if ev.getType().name() == "TUMBLE":
                    tumble_off = float(ev.getTime())
                    break
            if tumble_off is not None:
                break

        assert tumble_off is not None, "TUMBLE event should occur without listener"

        # With listener
        doc2 = _load_ork_doc(ork_path)
        sim2 = doc2.getSimulations().get(0)
        sim2.getOptions().setRandomSeed(SIM_SEED)
        _seed_multilevel_wind(sim2.getOptions(), SIM_SEED)
        listener = _get_anti_tumble_listener()
        sim2.simulate(listener)
        data_on = sim2.getSimulatedData()

        ground_hit_on = False
        for bi in range(int(data_on.getBranchCount())):
            for ev in data_on.getBranch(bi).getEvents():
                if ev.getType() == FlightEvent.Type.GROUND_HIT:
                    ground_hit_on = True
                    break
            if ground_hit_on:
                break

        assert ground_hit_on, "With listener, simulation should reach ground contact"
