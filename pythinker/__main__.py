"""
Entry point for running pythinker as a module: python -m pythinker
"""

# Logging bootstrap happens at the top of ``pythinker.cli.commands`` so it
# runs for both ``python -m pythinker`` (this path) AND the installed
# ``pythinker`` console-script (which goes through entry-points and skips
# this file entirely). Don't re-bootstrap here — that would just be a
# second logger.remove() / add() pair with no effect.
from pythinker.cli.commands import app

if __name__ == "__main__":
    app()
