"""Entry point: python -m applypilot.discovery.smartextract [--workers N] [--debug]"""

import argparse
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SmartExtract discovery")
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=os.cpu_count(),
        help=f"Parallel site workers (default: cpu count = {os.cpu_count()})",
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger("applypilot").setLevel(logging.DEBUG)

    from applypilot.discovery.smartextract import run_smart_extract

    result = run_smart_extract(workers=args.workers)
    print(f"\nDone: {result}")


if __name__ == "__main__":
    main()
