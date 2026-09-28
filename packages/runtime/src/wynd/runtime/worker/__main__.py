"""`python -m wynd.runtime.worker`: one venv worker process, spoken to over stdio (PLAN §3.12)."""

from wynd.runtime.worker.server import main

if __name__ == "__main__":
    main()
