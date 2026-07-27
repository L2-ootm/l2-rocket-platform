"""Pre-flight geometry validator for OSIFOG direct-driver batches.

`generate_ork` is pure XML generation (no JVM), so a candidate parameter set can
be checked for generator/geometry violations in milliseconds instead of burning
a ~40 s OpenRocket simulation slot on a GEN-FAIL. Use this to filter a candidate
grid BEFORE writing a batch json for scripts/osifog_direct_driver.py.

Usage:
  venv/Scripts/python.exe -X utf8 scripts/osifog_preflight.py <batch.json>
    -> reports OK / FAIL per tag, and writes <batch>.ok.json containing only
       the tags that generate cleanly.
"""
import sys, os, json, copy

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
os.chdir(_REPO)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osifog_direct_driver import BASE  # same BASE the driver uses
from osifog_sweep import generate_ork


def check(overrides):
    p = copy.deepcopy(BASE)
    p.update(overrides)
    p.pop("_s0_flaps_on_nose_m", None)
    try:
        generate_ork(p)
        return None
    except Exception as e:
        return str(e)


def main():
    batch_path = sys.argv[1]
    batch = json.load(open(batch_path))
    seeds = batch.pop("_seeds", [16000])
    common_overrides = batch.pop("_base", {})
    ok = {"_seeds": seeds}
    if common_overrides:
        ok["_base"] = common_overrides
    for tag, overrides in batch.items():
        err = check({**common_overrides, **overrides})
        if err is None:
            ok[tag] = overrides
            print("OK   %s" % tag, flush=True)
        else:
            print("FAIL %-18s %s" % (tag, err[:220]), flush=True)
    dst = batch_path.replace(".json", ".ok.json")
    json.dump(ok, open(dst, "w"), indent=1)
    print("\n%d/%d generate cleanly -> %s" % (len(batch) - sum(
        1 for tag in batch if tag not in ok
    ), len(batch), dst), flush=True)


if __name__ == "__main__":
    main()
