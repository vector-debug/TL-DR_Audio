"""InterIIT meeting assistant package."""


def main() -> int:
    """Load and run the CLI without importing it during module discovery."""

    from interiit.cli import main as cli_main

    return cli_main()


__all__ = ["main"]
