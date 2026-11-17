"""Run only the revised VOSA verification measurements on a CPU."""
import argparse
from pathlib import Path
import sys

from experiments import align, sis_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "run", "check", "status", "verify"))
    parser.add_argument("--include-grid", action="store_true", help="include the VOSA-only dropout figures")
    parser.add_argument("--resume", type=Path, help="continue an interrupted formal or smoke run")
    parser.add_argument("--run", type=Path, help="result directory to verify or inspect")
    parser.add_argument("--smoke", action="store_true", help="small suite wiring check")
    args = parser.parse_args()
    if args.resume and args.action != "run":
        parser.error("--resume requires run")
    if bool(args.run) != (args.action in ("status", "verify")):
        parser.error("--run is required for status/verify only")
    directory = args.run or args.resume
    cohort = "vosa-grid" if args.include_grid else "vosa-matched"
    if directory:
        contract = align.read(directory / "manifest.json")["contract"]
        recorded = contract["host"].get("verification_cohort")
        if not sis_suite.is_current(directory) or recorded not in ("vosa-matched", "vosa-grid"):
            parser.error("Select the result directory from a revised VOSA-only run")
        if (args.include_grid and recorded != "vosa-grid") or (args.smoke and not contract["smoke"]):
            parser.error("The supplied options differ from the recorded run")
        cohort, args.smoke = recorded, contract["smoke"]
    sys.argv = [sys.argv[0], args.action, "--scope", "verification"]
    if args.smoke:
        sys.argv.append("--smoke")
    if directory:
        sys.argv.extend(["--resume" if args.resume else "--run", str(directory)])
    sis_suite.load(cohort).main()


if __name__ == "__main__":
    main()
