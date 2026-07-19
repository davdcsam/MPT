# Install status

Verified 2026-07-15: project is fully installed.

- `.venv` matches `uv.lock` exactly — `uv sync --frozen --dry-run` reports "Checked 122 packages... would make no changes" (254 packages present in `.venv/lib/python3.11/site-packages`).
- No separate frontend build needed — `webui/` is a Streamlit app (`Main.py`), not a Node project; no `package.json` exists anywhere in the repo.
- `config.toml` already exists (copied from `config.example.toml`) — still worth checking it has real API keys/credentials filled in, since that's a configuration step, not an install step.

To run:
- Web UI: `sh webui.sh` (or `streamlit run webui/Main.py`)
- API: `python main.py`