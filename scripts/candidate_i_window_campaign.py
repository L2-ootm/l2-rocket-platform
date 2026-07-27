#!/usr/bin/env python3
"""Automated, immutable Candidate I landing-window authority campaign.

The candidate package is never edited.  Every trial is generated in memory
from candidate_I.json, with only ignition delays, random seed, or diagnostic
timestep varied.  OpenRocket remains the authority for every result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from motor_data import load_motor_by_index
from osifog_sweep import (
    init_or,
    parse_wind_csv,
    run_sim,
    score_official,
)

CANDIDATE_JSON = REPO / "designs/osifog_submission/candidate_I.json"
CANDIDATE_ORK = REPO / "designs/osifog_submission/candidate_I.ork"
EXPECTED_HASHES = {
    "candidate_I.json": "44441616D5774A4630918374FBEEB61EDBD0415CB7803F1125323C5F583F906E",
    "candidate_I.ork": "74B54EE7AF06E81AFFAE722625398C89CE3F226D1913BB7F4F1C4CBBF7B57172",
}
STAGES = {
    "s0": {"branch": 0, "delay_key": "s0_retro_delay", "motor_key": "s0_retro"},
    "s1": {"branch": 1, "delay_key": "s1_retro_delay", "motor_key": "s1_retro"},
}
OFFICIAL_SEED = 16000
OFFICIAL_TIMESTEP_S = 0.05


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def verify_immutable_package(parameters: dict) -> dict:
    actual = {
        CANDIDATE_JSON.name: sha256(CANDIDATE_JSON),
        CANDIDATE_ORK.name: sha256(CANDIDATE_ORK),
    }
    if actual != EXPECTED_HASHES:
        raise RuntimeError(
            f"Candidate I package hash drift: expected {EXPECTED_HASHES}, got {actual}"
        )
    required = {
        "s0_main": None,
        "s1_main": 20,
        "octaweb_rings": True,
        "interstage_coupler": True,
    }
    mismatches = {
        key: {"expected": value, "actual": parameters.get(key)}
        for key, value in required.items()
        if parameters.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"Candidate I architecture drift: {mismatches}")
    return actual


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _impulse(points: list[tuple[float, float]], end_s: float | None = None) -> float:
    total = 0.0
    for (t0, f0), (t1, f1) in zip(points, points[1:]):
        if end_s is not None and t0 >= end_s:
            break
        right = t1 if end_s is None else min(t1, end_s)
        if right <= t0:
            continue
        fraction = (right - t0) / (t1 - t0)
        f_right = f0 + fraction * (f1 - f0)
        total += (f0 + f_right) * 0.5 * (right - t0)
        if end_s is not None and right >= end_s:
            break
    return total


def remaining_propellant_g(motor, elapsed_s: float) -> float:
    points = list(zip(motor.time_points_s, motor.thrust_points_n))
    total = _impulse(points)
    consumed = _impulse(points, max(0.0, elapsed_s))
    remaining_fraction = 0.0 if total <= 0.0 else max(0.0, 1.0 - consumed / total)
    return 1000.0 * motor.propellant_mass_kg * remaining_fraction


class Campaign:
    def __init__(self, output_dir: Path, quick: bool = False):
        self.output_dir = output_dir
        self.result_path = output_dir / "campaign.json"
        self.report_path = output_dir / "REPORT.md"
        self.quick = quick
        self.parameters = json.loads(CANDIDATE_JSON.read_text(encoding="utf-8"))
        self.hashes_before = verify_immutable_package(self.parameters)
        # Candidate I predates the compiler making this derived octaweb value
        # mandatory in the parameter mapping.  Supply it only to in-memory
        # trials; the locked candidate JSON is intentionally not rewritten.
        self.parameters["main_cluster_count"] = 3
        self.parameters["wind_levels"] = parse_wind_csv("OSIFOG/OpenWind_File.csv")
        with zipfile.ZipFile(CANDIDATE_ORK) as archive:
            self.template_xml = archive.read("rocket.ork").decode("utf-8")
        self.motors = {
            stage: load_motor_by_index(int(self.parameters[spec["motor_key"]]))
            for stage, spec in STAGES.items()
        }
        self.rows: dict[str, dict] = {}
        if self.result_path.exists():
            saved = json.loads(self.result_path.read_text(encoding="utf-8"))
            if saved.get("candidate_hashes_before") == self.hashes_before:
                self.rows = saved.get("rows", {})
        self.evaluation_count = len(self.rows)

    def trial_xml(
        self, s0_delay: float, s1_delay: float, timestep_s: float
    ) -> str:
        """Return an in-memory timing variant of the locked saved authority XML."""
        xml = self.template_xml
        for mount_name, delay in (
            ("Sustainer Structural Retro Sleeve", s0_delay),
            ("Booster Structural Retro Sleeve", s1_delay),
        ):
            pattern = re.compile(
                rf"(<innertube>\s*<name>{re.escape(mount_name)}</name>.*?</innertube>)",
                re.DOTALL,
            )
            match = pattern.search(xml)
            if match is None:
                raise RuntimeError(f"locked ORK is missing {mount_name}")
            block, count = re.subn(
                r"<ignitiondelay>[^<]+</ignitiondelay>",
                f"<ignitiondelay>{delay:.6f}</ignitiondelay>",
                match.group(1),
            )
            if count != 2:
                raise RuntimeError(
                    f"{mount_name} expected two serialized ignition delays, got {count}"
                )
            xml = xml[: match.start()] + block + xml[match.end() :]
        xml, count = re.subn(
            r"<timestep>[^<]+</timestep>",
            f"<timestep>{timestep_s:.6f}</timestep>",
            xml,
        )
        if count < 1:
            raise RuntimeError("locked ORK contains no simulation timestep")
        return xml

    @staticmethod
    def _key(delays: dict, seed: int, timestep_s: float) -> str:
        return (
            f"s0={delays['s0']:.6f}|s1={delays['s1']:.6f}|"
            f"seed={seed}|dt={timestep_s:.6f}"
        )

    def save(self, phase: str, analysis: dict | None = None) -> None:
        _atomic_json(
            self.result_path,
            {
                "schema": 1,
                "status": "running",
                "phase": phase,
                "candidate": "Candidate I",
                "candidate_hashes_before": self.hashes_before,
                "architecture_lock": {
                    "s0_main": None,
                    "s1_main_cluster_count": 3,
                    "external_pods": False,
                    "octaweb_rings": True,
                    "interstage_coupler": True,
                },
                "official_seed": OFFICIAL_SEED,
                "official_timestep_s": OFFICIAL_TIMESTEP_S,
                "rows": self.rows,
                "analysis": analysis,
            },
        )

    def evaluate(
        self,
        s0_delay: float,
        s1_delay: float,
        *,
        seed: int = OFFICIAL_SEED,
        timestep_s: float = OFFICIAL_TIMESTEP_S,
        phase: str,
    ) -> dict:
        delays = {"s0": round(float(s0_delay), 6), "s1": round(float(s1_delay), 6)}
        key = self._key(delays, seed, timestep_s)
        if key in self.rows:
            return self.rows[key]

        p = dict(self.parameters)
        p["s0_retro_delay"] = delays["s0"]
        p["s1_retro_delay"] = delays["s1"]
        p["timestep_s"] = float(timestep_s)
        xml = self.trial_xml(delays["s0"], delays["s1"], timestep_s)
        metrics = run_sim(xml, seed=seed)
        try:
            official = score_official(metrics, p)
        except Exception as exc:
            official = {"is_legal": False, "error": str(exc)}

        landings = {
            int(item["branch"]): item for item in metrics.get("stage_landings", [])
        }
        stage_results = {}
        for stage, spec in STAGES.items():
            landing = landings.get(spec["branch"])
            delay = delays[stage]
            motor = self.motors[stage]
            if landing is None:
                stage_results[stage] = {
                    "landing_found": False,
                    "legal": False,
                }
                continue
            contact = float(landing["time_s"])
            elapsed = max(0.0, contact - delay)
            burnout = delay + motor.burn_duration_s
            speed = float(landing["total_speed"])
            stage_results[stage] = {
                "landing_found": True,
                "branch": spec["branch"],
                "motor": motor.designation,
                "configured_delay_s": delay,
                "contact_time_s": contact,
                "touchdown_speed_mps": speed,
                "legal": speed < 5.0,
                "burn_duration_s": motor.burn_duration_s,
                "burnout_time_s": burnout,
                "burnout_minus_contact_s": burnout - contact,
                "burning_at_contact": burnout >= contact - 1.0e-9,
                "estimated_propellant_remaining_at_contact_g": (
                    remaining_propellant_g(motor, elapsed)
                ),
                "east_m": float(landing["east_m"]),
                "north_m": float(landing["north_m"]),
                "orientation_theta_deg": float(landing["orientation_theta_deg"]),
            }

        row = {
            "phase": phase,
            "seed": seed,
            "timestep_s": timestep_s,
            "delays_s": delays,
            "apogee_m": float(metrics.get("apogee_m", math.nan)),
            "mach": float(metrics.get("mach", math.nan)),
            "stages": stage_results,
            "mission_legal": bool(official.get("is_legal", False)),
            "official_score": official.get("score"),
            "official_raw_score": official.get("raw_score"),
            "official_violations": official.get("violations", []),
        }
        self.rows[key] = row
        self.evaluation_count += 1
        speeds = "/".join(
            f"{stage}:{stage_results.get(stage, {}).get('touchdown_speed_mps', math.nan):.3f}"
            for stage in ("s0", "s1")
        )
        print(
            f"[{self.evaluation_count}] {phase} s0={delays['s0']:.6f} "
            f"s1={delays['s1']:.6f} seed={seed} dt={timestep_s:g} "
            f"v={speeds} legal={row['mission_legal']}",
            flush=True,
        )
        self.save(phase)
        return row

    def _stage_rows(self, stage: str) -> list[dict]:
        other = "s1" if stage == "s0" else "s0"
        baseline_other = float(self.parameters[f"{other}_retro_delay"])
        values = []
        for row in self.rows.values():
            if row["seed"] != OFFICIAL_SEED or row["timestep_s"] != OFFICIAL_TIMESTEP_S:
                continue
            if abs(row["delays_s"][other] - baseline_other) > 1.0e-9:
                continue
            if row["stages"].get(stage, {}).get("landing_found"):
                values.append(row)
        return sorted(values, key=lambda item: item["delays_s"][stage])

    def sweep_stage(self, stage: str) -> None:
        baseline = {key: float(self.parameters[f"{key}_retro_delay"]) for key in STAGES}
        if self.quick:
            offsets = {Decimal("-0.005"), Decimal("0"), Decimal("0.005")}
        else:
            offsets = {
                Decimal(i) * Decimal("0.005") for i in range(-20, 21)
            }
            offsets.update(
                Decimal(i) * Decimal("0.0005") for i in range(-40, 41)
            )
        for offset in sorted(offsets):
            delays = dict(baseline)
            delays[stage] = baseline[stage] + float(offset)
            self.evaluate(**{
                "s0_delay": delays["s0"],
                "s1_delay": delays["s1"],
                "phase": f"{stage}_authority_map",
            })

        if self.quick:
            return
        rows = self._stage_rows(stage)
        candidates = []
        for left, right in zip(rows, rows[1:]):
            gap = right["delays_s"][stage] - left["delays_s"][stage]
            # Refine the campaign's original 0.5 ms mesh once.  Checkpoint
            # resumes must not recursively halve already-refined intervals.
            if gap < 0.00049 or gap > 0.00051:
                continue
            left_stage = left["stages"][stage]
            right_stage = right["stages"][stage]
            if (
                left_stage["legal"]
                or right_stage["legal"]
                or min(
                    left_stage["touchdown_speed_mps"],
                    right_stage["touchdown_speed_mps"],
                ) < 8.0
            ):
                candidates.append(
                    (left["delays_s"][stage] + right["delays_s"][stage]) / 2.0
                )
        for delay in candidates:
            delays = dict(baseline)
            delays[stage] = delay
            self.evaluate(**{
                "s0_delay": delays["s0"],
                "s1_delay": delays["s1"],
                "phase": f"{stage}_adaptive_refine",
            })

    def components(self, stage: str) -> list[dict]:
        rows = self._stage_rows(stage)
        components: list[list[dict]] = []
        active: list[dict] | None = None
        for row in rows:
            if not row["stages"][stage]["legal"]:
                # An explicitly sampled illegal point splits the basin even
                # when legal samples exist on both sides.
                active = None
                continue
            if (
                active is None
                or row["delays_s"][stage]
                - active[-1]["delays_s"][stage]
                > 0.00051
            ):
                components.append([row])
                active = components[-1]
            else:
                active.append(row)
        result = []
        for group in components:
            delays = [row["delays_s"][stage] for row in group]
            speeds = [row["stages"][stage]["touchdown_speed_mps"] for row in group]
            result.append(
                {
                    "start_s": min(delays),
                    "end_s": max(delays),
                    "sampled_width_ms": 1000.0 * (max(delays) - min(delays)),
                    "sample_count": len(group),
                    "worst_touchdown_speed_mps": max(speeds),
                    "best_touchdown_speed_mps": min(speeds),
                    "center_s": 0.5 * (min(delays) + max(delays)),
                }
            )
        return result

    def preferred_delay(self, stage: str) -> float:
        components = self.components(stage)
        if components:
            best = max(
                components,
                key=lambda item: (
                    item["sampled_width_ms"],
                    -item["worst_touchdown_speed_mps"],
                ),
            )
            return float(best["center_s"])
        rows = self._stage_rows(stage)
        return min(
            rows, key=lambda row: row["stages"][stage]["touchdown_speed_mps"]
        )["delays_s"][stage]

    def coupled_campaign(self) -> tuple[float, float]:
        centers = {stage: self.preferred_delay(stage) for stage in STAGES}
        offsets = (0.0,) if self.quick else (-0.005, -0.0025, 0.0, 0.0025, 0.005)
        joint_rows = []
        for s0_offset in offsets:
            for s1_offset in offsets:
                joint_rows.append(
                    self.evaluate(
                        centers["s0"] + s0_offset,
                        centers["s1"] + s1_offset,
                        phase="coupled_window",
                    )
                )
        legal = [row for row in joint_rows if row["mission_legal"]]
        pool = legal or joint_rows
        best = min(
            pool,
            key=lambda row: (
                max(
                    row["stages"]["s0"].get("touchdown_speed_mps", 1.0e9),
                    row["stages"]["s1"].get("touchdown_speed_mps", 1.0e9),
                ),
                sum(
                    abs(row["stages"][stage].get("burnout_minus_contact_s", 1.0e9))
                    for stage in STAGES
                ),
            ),
        )
        return best["delays_s"]["s0"], best["delays_s"]["s1"]

    def robustness_campaign(self, center: tuple[float, float]) -> None:
        if self.quick:
            return
        s0, s1 = center
        perturbations = (
            (0.0, 0.0),
            (-0.005, 0.0),
            (0.005, 0.0),
            (0.0, -0.005),
            (0.0, 0.005),
        )
        for seed in range(OFFICIAL_SEED, OFFICIAL_SEED + 5):
            for ds0, ds1 in perturbations:
                self.evaluate(
                    s0 + ds0,
                    s1 + ds1,
                    seed=seed,
                    phase="seed_robustness",
                )
        for timestep in (0.02, 0.01, 0.005):
            for ds0, ds1 in perturbations:
                self.evaluate(
                    s0 + ds0,
                    s1 + ds1,
                    timestep_s=timestep,
                    phase="timestep_convergence",
                )

    def analysis(self, center: tuple[float, float]) -> dict:
        legal_rows = [row for row in self.rows.values() if row["mission_legal"]]
        last_gram = {}
        for stage in STAGES:
            candidates = [
                row for row in legal_rows
                if row["stages"].get(stage, {}).get("landing_found")
            ]
            if candidates:
                best = min(
                    candidates,
                    key=lambda row: abs(
                        row["stages"][stage]["burnout_minus_contact_s"]
                    ),
                )
                last_gram[stage] = {
                    "delays_s": best["delays_s"],
                    "seed": best["seed"],
                    "timestep_s": best["timestep_s"],
                    **best["stages"][stage],
                }
        center_rows_by_seed = {}
        for seed in range(OFFICIAL_SEED, OFFICIAL_SEED + 5):
            key = self._key(
                {"s0": center[0], "s1": center[1]},
                seed,
                OFFICIAL_TIMESTEP_S,
            )
            row = self.rows.get(key)
            if row is not None:
                center_rows_by_seed[str(seed)] = {
                    "mission_legal": row["mission_legal"],
                    "s0_touchdown_speed_mps": row["stages"]["s0"].get(
                        "touchdown_speed_mps"
                    ),
                    "s1_touchdown_speed_mps": row["stages"]["s1"].get(
                        "touchdown_speed_mps"
                    ),
                }
        center_rows_by_timestep = {}
        for timestep in (0.05, 0.02, 0.01, 0.005):
            key = self._key(
                {"s0": center[0], "s1": center[1]},
                OFFICIAL_SEED,
                timestep,
            )
            row = self.rows.get(key)
            if row is not None:
                center_rows_by_timestep[str(timestep)] = {
                    "mission_legal": row["mission_legal"],
                    "s0_touchdown_speed_mps": row["stages"]["s0"].get(
                        "touchdown_speed_mps"
                    ),
                    "s1_touchdown_speed_mps": row["stages"]["s1"].get(
                        "touchdown_speed_mps"
                    ),
                }
        perturbations = {
            "s0_minus_5ms": (center[0] - 0.005, center[1]),
            "s0_plus_5ms": (center[0] + 0.005, center[1]),
            "s1_minus_5ms": (center[0], center[1] - 0.005),
            "s1_plus_5ms": (center[0], center[1] + 0.005),
        }
        five_ms_results = {}
        for label, delays in perturbations.items():
            key = self._key(
                {"s0": delays[0], "s1": delays[1]},
                OFFICIAL_SEED,
                OFFICIAL_TIMESTEP_S,
            )
            row = self.rows.get(key)
            if row is not None:
                five_ms_results[label] = {
                    "mission_legal": row["mission_legal"],
                    "s0_touchdown_speed_mps": row["stages"]["s0"].get(
                        "touchdown_speed_mps"
                    ),
                    "s1_touchdown_speed_mps": row["stages"]["s1"].get(
                        "touchdown_speed_mps"
                    ),
                }
        return {
            "evaluations": len(self.rows),
            "individual_stage_components": {
                stage: self.components(stage) for stage in STAGES
            },
            "selected_coupled_delays_s": {"s0": center[0], "s1": center[1]},
            "legal_evaluations": len(legal_rows),
            "closest_legal_burnout_to_touchdown": last_gram,
            "selected_center_by_seed": center_rows_by_seed,
            "selected_center_seed_pass_count": sum(
                item["mission_legal"] for item in center_rows_by_seed.values()
            ),
            "selected_center_by_timestep": center_rows_by_timestep,
            "selected_center_timestep_pass_count": sum(
                item["mission_legal"]
                for item in center_rows_by_timestep.values()
            ),
            "selected_center_5ms_perturbations": five_ms_results,
            "target_contiguous_window_ms": 10.0,
            "target_met": all(
                any(
                    component["sampled_width_ms"] >= 10.0
                    for component in self.components(stage)
                )
                for stage in STAGES
            ),
        }

    def write_report(self, analysis: dict, hashes_after: dict) -> None:
        lines = [
            "# Candidate I immutable landing-window campaign",
            "",
            f"- OpenRocket evaluations: {analysis['evaluations']}",
            f"- Candidate JSON SHA-256 unchanged: `{hashes_after['candidate_I.json']}`",
            f"- Candidate ORK SHA-256 unchanged: `{hashes_after['candidate_I.ork']}`",
            "- Architecture: internal octaweb; no external pods; retro-only Sustainer; coupler retained.",
            f"- 10 ms contiguous-window target met on both stages: **{analysis['target_met']}**",
            "",
            "## Sampled legal components",
            "",
        ]
        for stage in ("s0", "s1"):
            lines.append(f"### {stage}")
            lines.append("")
            components = analysis["individual_stage_components"][stage]
            if not components:
                lines.append("- No legal sampled component.")
            for component in components:
                lines.append(
                    "- "
                    f"{component['start_s']:.6f}–{component['end_s']:.6f} s; "
                    f"sampled width {component['sampled_width_ms']:.3f} ms; "
                    f"{component['sample_count']} samples; "
                    f"speed {component['best_touchdown_speed_mps']:.3f}–"
                    f"{component['worst_touchdown_speed_mps']:.3f} m/s."
                )
            lines.append("")
        lines.extend(
            [
                "## Burnout-at-contact check",
                "",
                "The closest legal trials are recorded below. Positive burnout-minus-contact means the motor was still burning at contact.",
                "",
            ]
        )
        for stage, item in analysis["closest_legal_burnout_to_touchdown"].items():
            lines.append(
                f"- {stage}: Δt={item['burnout_minus_contact_s'] * 1000.0:+.3f} ms; "
                f"estimated propellant remaining={item['estimated_propellant_remaining_at_contact_g']:.3f} g; "
                f"touchdown={item['touchdown_speed_mps']:.3f} m/s."
            )
        lines.extend(
            [
                "",
                "## Robustness",
                "",
                f"- Selected center: s0={analysis['selected_coupled_delays_s']['s0']:.6f} s, "
                f"s1={analysis['selected_coupled_delays_s']['s1']:.6f} s.",
                f"- Seed pass rate at the selected center: "
                f"{analysis['selected_center_seed_pass_count']}/"
                f"{len(analysis['selected_center_by_seed'])}.",
                f"- Timestep pass rate at the selected center: "
                f"{analysis['selected_center_timestep_pass_count']}/"
                f"{len(analysis['selected_center_by_timestep'])}.",
                "",
                "### Selected-center seed results",
                "",
            ]
        )
        for seed, item in analysis["selected_center_by_seed"].items():
            lines.append(
                f"- {seed}: legal={item['mission_legal']}; "
                f"s0={item['s0_touchdown_speed_mps']:.3f} m/s; "
                f"s1={item['s1_touchdown_speed_mps']:.3f} m/s."
            )
        lines.extend(["", "### Selected-center timestep results", ""])
        for timestep, item in analysis["selected_center_by_timestep"].items():
            lines.append(
                f"- {timestep} s: legal={item['mission_legal']}; "
                f"s0={item['s0_touchdown_speed_mps']:.3f} m/s; "
                f"s1={item['s1_touchdown_speed_mps']:.3f} m/s."
            )
        lines.extend(["", "### ±5 ms ignition perturbations", ""])
        for label, item in analysis["selected_center_5ms_perturbations"].items():
            lines.append(
                f"- {label}: legal={item['mission_legal']}; "
                f"s0={item['s0_touchdown_speed_mps']:.3f} m/s; "
                f"s1={item['s1_touchdown_speed_mps']:.3f} m/s."
            )
        self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def finalize(self, center: tuple[float, float]) -> dict:
        analysis = self.analysis(center)
        hashes_after = verify_immutable_package(
            json.loads(CANDIDATE_JSON.read_text(encoding="utf-8"))
        )
        payload = json.loads(self.result_path.read_text(encoding="utf-8"))
        payload.update(
            {
                "status": "complete",
                "phase": "finished",
                "analysis": analysis,
                "candidate_hashes_after": hashes_after,
            }
        )
        _atomic_json(self.result_path, payload)
        self.write_report(analysis, hashes_after)
        return analysis

    def run(self) -> dict:
        init_or()
        self.sweep_stage("s0")
        self.sweep_stage("s1")
        center = self.coupled_campaign()
        self.robustness_campaign(center)
        return self.finalize(center)

    def analyze_only(self) -> dict:
        if not self.result_path.exists():
            raise RuntimeError("analyze-only requires an existing campaign.json")
        saved = json.loads(self.result_path.read_text(encoding="utf-8"))
        selected = saved.get("analysis", {}).get("selected_coupled_delays_s")
        if not selected:
            raise RuntimeError("campaign has no selected coupled delays")
        return self.finalize((float(selected["s0"]), float(selected["s1"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("OSIFOG/experiments-2026-07-25/candidate_I_full_window_campaign"),
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="three-point smoke campaign instead of the full authority map",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="rebuild analysis/report from an existing checkpoint without OpenRocket",
    )
    args = parser.parse_args(argv)
    campaign = Campaign(args.output, quick=args.quick)
    analysis = campaign.analyze_only() if args.analyze_only else campaign.run()
    print(json.dumps(analysis, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
