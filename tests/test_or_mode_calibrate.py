from scripts.or_mode_calibrate import comparison_case, summarize


def test_compare_metrics_reports_rust_vs_openrocket_deltas():
    member = {
        "rust_apogee_m": 110.0,
        "rust_mach": 1.25,
        "rust_min_static_margin": 1.7,
        "score": 2.0,
    }
    official = {
        "status": "success",
        "apogee_m": 100.0,
        "mach": 1.0,
        "min_static_margin": 1.5,
        "flight_time_s": 12.0,
    }

    case = comparison_case(0, member, official)

    assert case["delta"]["apogee_m"] == 10.0
    assert case["delta"]["apogee_pct"] == 10.0
    assert case["delta"]["mach"] == 0.25
    assert case["delta"]["min_static_margin"] == 0.19999999999999996
    assert case["status"] == "success"


def test_summarize_calibration_cases():
    cases = [
        {"status": "success", "delta": {"apogee_pct": 10.0, "mach": 0.25, "min_static_margin": 0.2}},
        {"status": "success", "delta": {"apogee_pct": -4.0, "mach": -0.05, "min_static_margin": -0.1}},
    ]

    summary = summarize(cases)

    assert summary["count"] == 2
    assert summary["mean_abs_apogee_pct"] == 7.0
    assert summary["max_abs_apogee_pct"] == 10.0
    assert summary["mean_abs_mach"] == 0.15
    assert summary["max_abs_min_static_margin"] == 0.2
