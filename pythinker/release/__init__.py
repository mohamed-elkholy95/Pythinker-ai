"""Release-readiness helpers.

Used by:
- The ``pythinker release check`` CLI command.
- The ``publish.yml`` GitHub Actions workflow (importable + scriptable).
- Local pre-tag verification before ``gh release create``.

Default behaviour: only the cheap, repo-only checks run (PEP 440 version,
__init__ fallback equality, CHANGELOG section presence, optional git-tag
equality). The heavy checks (build, twine check, wheel install smoke) are
opt-in via ``--build`` because they touch pip / spawn subprocesses.
"""
