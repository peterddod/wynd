"""`python -m wynd.runtime.supervisor`: same as the `wynd-supervisor` console script."""

from wynd.runtime.supervisor.main import main

if __name__ == "__main__":
    raise SystemExit(main())
