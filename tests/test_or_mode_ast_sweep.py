from types import SimpleNamespace

from scripts.or_mode_ast_sweep import calibration_case, parse_seeds, run_sweep, summarize


def test_parse_seeds_accepts_spaced_csv():
    assert parse_seeds("1, 2,3") == [1, 2, 3]


def test_calibration_case_success_computes_proxy_vs_openrocket_delta():
    elite = {
        "score": 91.0,
        "rust_apogee_m": 1100.0,
        "rust_mach": 1.25,
        "rust_min_static_margin": 1.7,
        "ork": "designs/demo.ork",
    }
    or_metrics = {"status": "success", "apogee_m": 1000.0, "mach": 1.0, "flight_time_s": 42.0}

    case = calibration_case(7, 0, elite, or_metrics)

    assert case["status"] == "success"
    assert case["rust"]["apogee_m"] == 1100.0
    assert case["openrocket"]["apogee_m"] == 1000.0
    assert case["delta"]["apogee_m"] == 100.0
    assert case["delta"]["apogee_pct"] == 10.0
    assert case["delta"]["mach"] == 0.25


def test_calibration_case_failure_keeps_reason_without_fake_delta():
    elite = {"rust_apogee_m": 900.0, "rust_mach": 0.8, "ork": "designs/bad.ork"}
    or_metrics = {"status": "failed", "reason": "motor resolution failed"}

    case = calibration_case(8, 1, elite, or_metrics)

    assert case["status"] == "failed"
    assert case["reason"] == "motor resolution failed"
    assert case["openrocket"] is None
    assert case["delta"] == {"apogee_pct": None, "mach": None}


def test_summarize_reports_counts_and_abs_error_stats():
    cases = [
        {
            "status": "success",
            "delta": {"apogee_pct": 10.0, "mach": -0.2},
        },
        {
            "status": "success",
            "delta": {"apogee_pct": -20.0, "mach": 0.4},
        },
        {
            "status": "failed",
            "delta": {"apogee_pct": None, "mach": None},
        },
    ]

    summary = summarize(cases)

    assert summary["count"] == 3
    assert summary["success_count"] == 2
    assert summary["failure_count"] == 1
    assert summary["mean_abs_apogee_pct"] == 15.0
    assert summary["max_abs_apogee_pct"] == 20.0
    assert summary["mean_abs_mach"] == 0.30000000000000004
    assert summary["max_abs_mach"] == 0.4


def test_run_sweep_writes_zero_validation_report(monkeypatch, tmp_path):
    from scripts import or_mode_ast_sweep

    def fake_run_seed(seed, args):
        return {"seed": seed, "output_dir": str(args.out / f"seed_{seed}"), "payload": {}, "elites": []}

    monkeypatch.setattr(or_mode_ast_sweep, "run_seed", fake_run_seed)
    args = SimpleNamespace(
        seeds="11,12",
        population=4,
        elite_count=2,
        generations=1,
        target_apogee=1500.0,
        physics="openrocket",
        mission=None,
        jar=tmp_path / "OpenRocket-test.jar",
        validate_count=0,
        out=tmp_path / "out",
        ckg_dir=tmp_path / "ckg",
        report=tmp_path / "reports" / "sweep.json",
    )

    report = run_sweep(args)

    assert args.report.exists()
    assert report["seeds"] == [11, 12]
    assert report["summary"]["count"] == 0
