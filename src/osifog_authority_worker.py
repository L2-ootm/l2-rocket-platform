#!/usr/bin/env python3
"""Single-candidate OpenRocket authority worker for hard timeout isolation."""

from __future__ import annotations

from contextlib import redirect_stdout
import json
import math
import sys
import traceback


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        mode = request.get("mode", "official") if isinstance(request, dict) else "official"
        parameters = request.get("parameters", request)
        # OpenRocket/JPype startup logs are diagnostics, not protocol output.
        with redirect_stdout(sys.stderr):
            import osifog_engine_search as search
            if mode == "recovery_gate":
                tuned = dict(
                    parameters,
                    s0_retro_delay=0.0,
                    s1_retro_delay=0.0,
                    s0_retro_ignition_event="never",
                    s1_retro_ignition_event="never",
                )
                metrics = search._run_authority(
                    tuned, "STAGE_FREE_DESCENT_DIAGNOSTIC",
                    candidate_id=search._candidate_id(tuned),
                )
                official = {}
            else:
                metrics, official, tuned = search._default_openrocket_evaluator(parameters)
        print(json.dumps(_json_safe({
            "status": "success",
            "metrics": metrics,
            "official": official,
            "parameters": tuned,
        }), allow_nan=False))
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
