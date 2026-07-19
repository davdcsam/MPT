# Chatterbox Local TTS — status & handoff

## Context

The user wanted the "think before you speak" approach from a sibling local
project, `~/Projects/ThinkBeforeYouSpeak`, ported into MoneyPrinterTurbo,
replacing/augmenting the current TTS engine while **preserving all of
ThinkBeforeYouSpeak's Chatterbox customization** (per-tone voice cloning +
generation params) — not just swapping providers.

ThinkBeforeYouSpeak's pipeline: an LLM reads the full script, splits it into
short segments, and tags each with an emotional tone (from a closed catalog)
plus fine-tuned Chatterbox generation params (`exaggeration`, `cfg_weight`,
`temperature`) and pre/post pause timing. Only then does it synthesize audio,
segment by segment, cloning a different reference voice per segment
("think before you speak").

Three decisions were confirmed with the user before implementation:
1. Keep MoneyPrinterTurbo's existing HTTP-based `chatterbox:` provider
   (talks to a separate self-hosted server) completely untouched. Add the new
   in-process provider as a **separate** provider, `chatterbox-local:`.
2. The new provider is **opt-in**, not the new default. `[ui] tts_server`
   stays `azure-tts-v1`.
3. Bundle the 2 reference `.wav`s + a tone catalog into the repo so the
   provider works out-of-the-box (no required user setup).

## Status: implementation complete, PR open, real end-to-end test passed

- **Branch**: `worktree-chatterbox-local-tts` (pushed to `origin`)
- **PR**: https://github.com/davdcsam/MoneyPrinterTurbo/pull/3 (draft, OPEN)
- **Commits**:
  - `05c3186` — feat: add in-process Chatterbox Local provider with LLM tone planning
  - `13f1000` — fix: pin `setuptools<80` for resemble-perth compatibility
- Working directory: this is a **git worktree** at
  `/Users/davdcsam/Projects/MoneyPrinterTurbo/.claude/worktrees/chatterbox-local-tts`,
  separate from the user's main checkout at `/Users/davdcsam/Projects/MoneyPrinterTurbo`
  (which stays on `main`). The worktree is on branch
  `worktree-chatterbox-local-tts`.

### Known blocker for the user right now

The user tried `git checkout worktree-chatterbox-local-tts` from their main
checkout (`/Users/davdcsam/Projects/MoneyPrinterTurbo`, branch `main`) to
inspect the changes, and got:

```
fatal: 'worktree-chatterbox-local-tts' is already used by worktree at
'/Users/davdcsam/Projects/MoneyPrinterTurbo/.claude/worktrees/chatterbox-local-tts'
```

Git refuses to have the same branch checked out in two places at once. This
is still unresolved. Two ways to fix it, not yet chosen by the user:

1. **Check out a differently-named local branch** pointing at the same
   remote commits (does not require touching the worktree):
   ```
   git fetch origin
   git checkout -b review-chatterbox-local origin/worktree-chatterbox-local-tts
   ```
2. **Remove/exit the worktree** (via `ExitWorktree` tool, or manually
   `git worktree remove .claude/worktrees/chatterbox-local-tts` from the main
   checkout) to free up the branch name, then
   `git checkout worktree-chatterbox-local-tts` works directly. Only do this
   if no further work is planned in the worktree — check `git status` in the
   worktree first for anything uncommitted (should be clean; `plan.md` itself
   is the only file that may be new/uncommitted at handoff time, see below).

The next agent should ask the user which they prefer, or just default to
option 1 (non-destructive) if the user wants to keep iterating in this
worktree.

## What was implemented

### New package `app/services/chatterbox_local/`
- `__init__.py` — empty
- `tone_planner.py` — LLM tone-planning stage:
  - `load_tone_catalog(path=None)` — loads bundled or custom
    `tone_catalog.yaml`
  - `build_tone_planning_prompt(text, catalog)` — builds the full prompt
    (ported/genericized from ThinkBeforeYouSpeak's `prompt_default.txt`)
  - `plan_script_segments(text, catalog, use_llm_planning=True)` — calls
    `app.services.llm.generate_text_response()`, validates JSON, does one
    self-repair retry, **never raises** — falls back to
    `_fallback_single_segment()` on any failure or when
    `use_llm_planning=False`
  - `_extract_json`, `_validate_segments`, `_parse_and_validate`,
    `_fallback_single_segment` — internal helpers
- `engine.py` — Chatterbox model wrapper + audio assembly:
  - `resolve_device(preference="auto")` — lazy `import torch`, mps>cuda>cpu
  - `get_chatterbox_model(device_preference="auto")` — **module-level cached
    singleton**, lazy `from chatterbox.tts import ChatterboxTTS`
  - `resolve_reference_wav(tone, catalog, tone_dir)`,
    `resolve_generation_params(segment, catalog)` — per-segment override →
    tone's `base_params` → library default
  - `_run_model_generate(model, text, reference_wav, params)` and
    `_save_segment_wav(wav, sample_rate, path)` — isolated as separate
    functions specifically so tests can patch them without needing a real
    torch/chatterbox-tts install
  - `synthesize_segments(segments, catalog, tone_dir, device_preference,
    output_path)` — renders all segments, pads pre/post pauses with pydub
    silence, concatenates, exports to mp3; returns
    `list[(text, spoken_duration_seconds, pre_pause_sec, post_pause_sec)]`
    for accurate subtitle timing
- `assets/tone_catalog.yaml` — 6 tones (GANCHO, INTRODUCCION, EXPLICACION,
  EXPLICACION_ENERGICA, AFIRMATIVO, TRIUNFO), English field names, ported
  from ThinkBeforeYouSpeak's `catalogo_tonos.yaml`
- `assets/normal-explaining.wav`, `assets/impressive-happy.wav` — the 2
  reference voice-cloning wavs, copied from ThinkBeforeYouSpeak's `tone/`
  directory (note: filenames were cleaned up, e.g.
  `normal-explaninig.WAV` → `normal-explaining.wav`)

### `app/services/llm.py`
- Added `generate_text_response(prompt: str) -> str`, a thin public wrapper
  around the existing private `_generate_response()`, so `tone_planner.py`
  can reuse whichever LLM provider the user already has configured in
  `[app] llm_provider` (20+ providers supported) instead of requiring a
  second, hardcoded LLM client.

### `app/services/voice.py`
- Added `populate_submaker_from_segment_durations(sub_maker, segments)` —
  new helper (next to the existing `populate_legacy_submaker_with_full_text`)
  that builds accurate `.subs`/`.offset` cues from real per-segment
  durations instead of a character-count proportional split.
- Added `get_chatterbox_local_voices()` (returns
  `["chatterbox-local:default-Female"]`) and `is_chatterbox_local_voice()`
  (prefix check, confirmed no collision with the existing
  `is_chatterbox_voice`, since `"chatterbox-local:x".startswith("chatterbox:")`
  is `False`).
- Added `chatterbox_local_tts(text, voice, voice_file, voice_rate=1.0,
  voice_volume=1.0, model_id="")` — the new provider function. Reads
  `config.chatterbox_local` (`device`, `use_llm_planning`,
  `tone_catalog_path`), lazily imports the new subpackage, runs the tone
  planner, then `engine.synthesize_segments()`, and returns a `SubMaker` via
  the new helper. Catches `ImportError` (missing optional deps) and any
  other exception, logging and returning `None` rather than crashing the
  pipeline.
- Added a dispatcher branch in `tts()` for `chatterbox-local:` voices,
  placed before the existing `chatterbox:` branch.

### Config
- `app/config/config.py` — added `chatterbox_local = _cfg.get("chatterbox_local", {})`
  load, and persists it in `save_config()`.
- `config.example.toml` — new `[chatterbox_local]` section (`device`,
  `tone_catalog_path`, `use_llm_planning`) with explanatory comments, right
  after the existing `[chatterbox]` section. (`config.toml` itself is
  gitignored/local, not touched in the repo.)

### Dependencies
- `pyproject.toml` — new optional extra `chatterbox-local` (NOT in the base
  `dependencies` list):
  ```toml
  chatterbox-local = [
      "chatterbox-tts>=0.1.4",
      "torch>=2.2",
      "torchaudio>=2.2",
      "torchvision>=0.17",
      "setuptools<80",
  ]
  ```
  The `setuptools<80` pin was added **after** a real end-to-end test caught a
  genuine bug: `chatterbox-tts`'s transitive dependency `resemble-perth`
  imports `pkg_resources` at import time without a try/except, and
  `setuptools>=80` no longer ships `pkg_resources`, which broke model loading
  with `TypeError: 'NoneType' object is not callable` on
  `perth.PerthImplicitWatermarker()`. Confirmed setuptools `79.0.1` has
  `pkg_resources` and `80.10.2` does not. Verified fixed by re-running the
  real end-to-end test after adding the pin — it passed (see below).
- `requirements.txt` — no new lines (kept lightweight); one comment pointing
  to `uv sync --extra chatterbox-local`.
- `uv.lock` — regenerated (large diff due to a `uv` format/metadata upgrade
  unrelated to this feature, plus the genuinely new locked packages for the
  extra). `uv sync --frozen` (what CI runs) was verified to still succeed.

### WebUI (`webui/Main.py`)
- Added `("chatterbox-local", "Chatterbox TTS (Local)")` to the `tts_servers`
  dropdown list.
- Added a `filtered_voices` branch calling `voice.get_chatterbox_local_voices()`.
- Added a `_friendly()` display-name branch for `chatterbox-local:` voices.
- Added `_sync_chatterbox_local_config_from_session_state()` (mirroring the
  existing `_sync_chatterbox_config_from_session_state()` for the HTTP
  provider) and wired it into the "Play Voice" button handler, since the
  Chatterbox Local settings widgets render after that button in script order.
- Added a settings block (device selectbox, use-LLM-planning checkbox,
  tone-catalog-path text input, tips) gated on
  `selected_tts_server == "chatterbox-local"`.
- i18n: added the new keys to `webui/i18n/en.json` (required, base locale)
  **and** `webui/i18n/ru.json` (required — there's a test,
  `test/services/test_webui_i18n.py`, that specifically enforces the
  Russian locale covers every English key and every static `tr()` call in
  `Main.py`). Other locale files (de, es, id, pt, tr, vi, zh) were **not**
  updated — not enforced by any test, left as a follow-up if desired.

### Tests
- `test/services/test_voice.py` — 4 new tests:
  `test_chatterbox_local_voice_helpers`,
  `test_chatterbox_local_tts_dispatches_via_tts`,
  `test_chatterbox_local_tts_builds_submaker_from_segment_durations`,
  `test_chatterbox_local_tts_missing_dependency_returns_none`.
- `test/services/test_chatterbox_local.py` (new file) — 7 tests:
  `test_load_tone_catalog_bundled_default`,
  `test_plan_script_segments_parses_valid_llm_json`,
  `test_plan_script_segments_self_repairs_once_on_invalid_json`,
  `test_plan_script_segments_falls_back_after_repeated_failures`,
  `test_plan_script_segments_use_llm_planning_false_skips_llm_call`,
  `test_resolve_generation_params_merges_segment_tone_and_library_defaults`,
  `test_synthesize_segments_pads_pauses_and_returns_durations`.
- All mocked — **no test requires a real torch/chatterbox-tts install**.
- Full suite (`uv run python -m unittest discover -s test`): **195 tests
  pass**, including the pre-existing HTTP-based `chatterbox:` tests
  (unmodified, still passing).
- Note: a fresh checkout/worktree needs `mkdir -p storage/temp` for one
  pre-existing, unrelated Gemini test to pass (`storage/` is gitignored and
  not auto-created by that test file) — this is not something introduced by
  this change, just an environment gap in any fresh worktree.

## Real end-to-end verification (already done, not just unit tests)

After the user confirmed they wanted a real install + test (not just mocked
tests), the following was done in this worktree's `.venv`:

1. `uv sync --extra chatterbox-local` — installs `torch==2.6.0`,
   `torchaudio==2.6.0`, `torchvision==0.21.0`, `chatterbox-tts==0.1.7`
   (confirmed matching ThinkBeforeYouSpeak's own installed versions).
2. First real run failed with the `pkg_resources`/`setuptools` bug described
   above — root-caused and fixed (see Dependencies section).
3. Second real run succeeded: loaded the actual `ChatterboxTTS` model on MPS
   (Apple Silicon), generated 2 segments with two different tones (GANCHO →
   `impressive-happy.wav` reference, EXPLICACION → `normal-explaining.wav`
   reference), applied pause padding, exported a real mp3. Verified via
   `ffprobe`: valid mp3, 24kHz mono, 7.74s total duration, matching the
   expected sum of segment durations + pause.

This proves the whole pipeline works for real, not just under mocks.

## What's NOT done / possible next steps

- **WebUI manual click-through**: nobody has clicked "Chatterbox TTS
  (Local)" in the actual Streamlit WebUI yet and listened to the "Play
  Voice" preview through the browser. The underlying `voice.tts()` call path
  was verified for real via the standalone script above, but not through
  the Streamlit UI itself. If asked to fully verify, run
  `streamlit run webui/Main.py` (check for a project-specific run script/skill
  first), select "Chatterbox TTS (Local)", and click Play Voice.
- **Other locale files** (de, es, id, pt, tr, vi, zh) don't have the new
  Chatterbox Local translation keys — only en/ru do (ru because a test
  enforces it). Not blocking, just incomplete i18n coverage.
- **The git worktree/branch conflict** described above is unresolved — the
  user needs to pick an approach (new branch name vs. removing the worktree)
  before they can inspect the code in their main checkout.
- **PR is still in draft state** — needs the user's explicit go-ahead before
  marking ready for review / merging (per this session's operating rules,
  never merge or mark ready without being asked).
- Version pins in `pyproject.toml`'s `chatterbox-local` extra
  (`chatterbox-tts>=0.1.4`, `torch>=2.2`, etc.) are lower-bound-only; they
  resolved correctly against current PyPI metadata at the time of this work,
  but should be spot-checked again if much time has passed before merging.

## Key files for a fresh agent to read first

- `app/services/chatterbox_local/tone_planner.py` and `engine.py` — the core
  new logic
- `app/services/voice.py` — search for `chatterbox_local` to find all
  insertion points
- `test/services/test_chatterbox_local.py` — shows the expected
  behavior/contracts via tests
- This file (`plan.md`) — status and handoff notes; delete or move once no
  longer needed (it was requested by the user specifically for
  agent-to-agent handoff, not as permanent repo documentation)
