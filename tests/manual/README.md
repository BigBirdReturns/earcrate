# Manual end-to-end verification harnesses

These are NOT run by `run_gates.py` (they need a live server, a browser, and the
audio stack). They are the reproducible harnesses used to verify the first-minute
fixes end-to-end. Run them on a box with the deps installed:

    pip install -r requirements.txt playwright
    # audio analyzer needs a working numba/numpy combo, e.g.:
    #   pip install "numpy==2.0.2" "numba==0.60.0" "llvmlite==0.43.0" "librosa==0.10.2"

- `verify_http_live.py`  — boots `python -m earcrate --serve` in a temp workspace and
  drives every touched endpoint over HTTP (first-run signal, config save + no-nesting,
  token gate, doctor, preflight/playlist shapes).
- `verify_dom_browser.py` — drives the real page with Playwright/Chromium: first-run
  routes to Setup, Save workspace, workspace field binds to the root (no /work nesting),
  the heartbeat goes red on last_error, and a failed request surfaces a toast.

Hermetic, gate-runnable versions of the core assertions live in
`tests/test_first_minute_fixes.py`.
