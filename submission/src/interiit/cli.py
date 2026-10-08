"""Command-line interface for the reproducible demo."""

import argparse
import json
import logging
from pathlib import Path

from interiit.config import get_settings
from interiit.errors import PipelineError
from interiit.pipeline import run_demo


def main() -> int:
    """Run the demo CLI and return a process exit code."""

    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the offline InterIIT meeting demo.")
    parser.add_argument("--sample", type=Path, default=settings.sample_data)
    parser.add_argument("--out", type=Path, default=settings.output_dir)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = run_demo(
            args.sample,
            args.out,
            on_progress=lambda stage, message: logging.info("[%s] %s", stage, message),
        )
    except (OSError, PipelineError, ValueError, json.JSONDecodeError) as exc:
        logging.error("%s", exc)
        return 1
    print(json.dumps(manifest["metrics"], indent=2))
    print(f"Artifacts: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
