# Contributing to VibeThePlaylist

Thanks for your interest in contributing! Contributions are welcome — this guide
keeps them smooth for everyone.

## Before you start (discuss first)

This project is **open but gated**: for anything beyond a small fix (typo, tiny
bug), **please open a GitHub issue first** to discuss the idea before writing code.
This avoids duplicated or wasted effort and lets us agree on the approach. Small,
obvious fixes can go straight to a PR.

## Development setup

```bash
conda activate Spotify          # the project's environment
pip install -r requirements-dev.txt
```

See [`README.md`](README.md) for how the app works and [`CLAUDE.md`](CLAUDE.md)
for an architecture overview.

## Workflow

1. **Branch** off `main` — do not push directly to `main`.
   ```bash
   git checkout main && git pull
   git checkout -b feature/short-description
   ```
2. Make your change.
3. **Run the tests** and make sure they pass; add tests for new behavior.
   ```bash
   python -m pytest
   ```
4. **Open a pull request** against `main` with a clear description of **what**
   changed and **why** (link the related issue if there is one).

## Pull request requirements

A PR should:

- [ ] Pass the full test suite (`python -m pytest`) and include tests for any new
      behavior.
- [ ] Come from a feature branch via PR (no direct commits to `main`).
- [ ] Have a description explaining the change and its motivation; reference the
      issue it addresses where applicable.

## Style

Match the surrounding code: module docstrings, type hints, and the
**config-driven design** — new genres, vibes, energy bands, or rules belong in
`config.py`, not hard-coded in logic. Keep network stages incremental (add a
`*_missing_*` DB helper so re-runs don't re-fetch known data).

## Reporting bugs / requesting features

Open an issue describing the problem or idea, with steps to reproduce for bugs
(and your Python/OS if relevant). Please don't include any secrets — never paste
the contents of your `.env` or token cache.

## License

By contributing, you agree that your contributions will be licensed under the
project's [MIT License](LICENSE.md).
