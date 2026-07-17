# Random Favs Browser

Fun Time can open a Chrome window on the left side of the secondary monitor.

How it works:

- Fun Time reads the favourites from `favs.csv` (written by the lock hotkey), which pairs each favourite's local video path with its gallery link.
- At launch, it picks a random subset of 10 and resolves where each one should open (see below).
- It launches a project-local Chrome shortcut.
- It waits for the new Chrome window and sizes it to the full left third of the secondary monitor.
- `Ctrl+Alt+Q` closes the RFB Chrome window gracefully (sends WM_CLOSE to the captured hwnd).

Configuration lives in `fun_time_config.json` under `random_favs_browser` (the Chrome shortcut path, the profile to target, and the favourites file).

### Where a tab actually goes

A favourite's gallery link is usually dead — the generation provider does not keep old generations around. So each tab resolves to the provider's **regenerate** page, with the video's original prompts packed into a `#ft=` fragment that the userscript below fills in. Favourites with no metadata sidecar (a provider that keeps its galleries, or anything scraped before sidecars existed) fall back to their stored gallery link. `target_for_fav` in `fun_time/random_favs_browser.py` is the single resolver; both the startup tabs and the lock hotkey go through it, so they cannot drift apart.

### The landing page

A tab first lands on a small local page ("Press Ctrl+R to load, or click the link") that names the favourite, shows where it points, and **plays the clip you are deciding whether to recreate**; the first reload or click navigates to the real destination. Ten heavy generate pages therefore do not all load at startup, and a lock never dumps you straight onto the provider either. `lazy_load` governs the startup tabs; a lock always lands here, because the landing page is what shows you the clip.

The destination cannot travel on Chrome's command line: a regenerate URL runs to ~4 KB of encoded prompt, and ten of them overflow the 32,767-character ceiling `CreateProcess` puts on a command line (`WinError 206`). So `fun_time/rfb_tab_page.py` bakes each destination into its own generated page under `state/rfb_tabs/`, and Chrome is handed short `file://` URIs. Startup writes `tab_NN.html`; a lock writes `lock_<hash>.html`, named after its destination so re-locking one video rewrites one page. The whole directory is cleared at the start of every session.

The clip is played straight from `file://` — muted, looped, and paused whenever its tab is not the visible one (ten background decoders is real CPU). When a clip cannot load, the page leaves it hidden and the rest of the tab still works.

It is **not** the video the favourite records. Every library video exists twice: the source original under `1_sorted/<source>/<orientation>/`, and the Topaz upscale under `2_outbox/upscaled_by_orientation/<orientation>/<source>/` with a `_topaz` suffix, which is what plays full-size and what `favs.csv` stores. The upscales are hundreds of megabytes of **HEVC** — a codec Chrome decodes only through a platform decoder, and in practice not at these resolutions — while the originals are a couple of megabytes of H.264 that any Chrome plays, `--disable-gpu` included. `fun_time/media_renditions.py` maps one to the other (note the two trees nest source and orientation in opposite orders); the clip is the original, and a video with no original on disk falls back to itself.

## Prompt autofill userscript

When you lock an AI video, or open one of its favourites in the Random Favs Browser, Fun Time opens the provider's **generate** page (instead of the now-dead gallery link) with the original prompts/settings packed into the URL fragment (`#ft=…`). A Tampermonkey userscript reads that fragment and fills the generate form — prompts, seed, and whatever settings it can match — then pins a floating note listing every field so anything it could not set can be entered by hand. Text-to-video and image-to-video are handled slightly differently (an image-to-video regen makes the image first, then the video from it), and the script re-applies fields for a few seconds because the form re-mounts during hydration.

The prompts/settings come from per-video metadata JSON mirrored under `regen.metadata_root`; the paths and the two generate URLs are configured in `fun_time_config.json` under `regen`. Videos without a metadata sidecar fall back to their stored gallery link.

The autofill script is **provider-specific** — its `@match` host and its form selectors target one particular generation site — so the real `fun_time/static/regen_autofill.user.js` is git-ignored and kept out of the public repo. A committed template, `fun_time/static/regen_autofill.example.user.js`, documents the `#ft=` payload shape and the auto-update wiring; copy it to `regen_autofill.user.js` and adapt the selectors to your provider.

### One-time setup: install the userscript

1. Install a userscript manager (e.g. Tampermonkey) in the Chrome profile the Random Favs Browser targets.
2. Copy `regen_autofill.example.user.js` to `fun_time/static/regen_autofill.user.js`, adapt it to your provider, and add it as a new userscript (Tampermonkey → Create new / Import).
3. Done — locking a video, or triggering one of its Random Favs Browser tabs, now opens the prefilled generate page.

The userscript only acts when the URL carries a `#ft=` fragment (i.e. opened by Fun Time); normal browsing on the provider is unaffected.

### Updating or maintaining the userscript

The script **auto-updates over localhost**. It carries `@updateURL` / `@downloadURL` pointing at `http://127.0.0.1:8770/regen_autofill.user.js`, and Fun Time's orchestrator serves that file (`fun_time/userscript_server.py`, a loopback-only daemon thread) whenever a session is running. Every edit bumps `@version`, so Tampermonkey pulls the new copy on its next update check. No more hand-pasting after the first install.

Environment facts (learned the hard way):

- Chrome's **"Allow user scripts"** toggle must be **ON** (recent Chrome versions gate userscripts behind it). If the script stops running *entirely* — no floating note appears at all on a locked video — re-check this first at `chrome://extensions`.
- **Do not** open the `.user.js` via a `file://` URL to install/update it — Chrome blocks that ("can't open scripts that way"). Auto-update goes through the localhost **http** server precisely because `file://` is blocked.

To pull a change (normal path):

1. Make sure a Fun Time session is running (that's what serves the script on `127.0.0.1:8770`).
2. Tampermonkey icon → **Check for userscript updates** (or wait for its scheduled check). The dashboard version should tick up to match the header's `@version`.
3. Test by locking a video. The script only runs on a `#ft=` URL and **strips the fragment after reading it**, so a plain reload won't re-run it — lock again (or re-paste a test URL) to re-test.

When the provider changes its UI (the usual cause of breakage), expect to tweak selectors in your local `regen_autofill.user.js`: prompts, settings, and the seed toggle are matched by placeholder/visible-text/icon heuristics, and old metadata value → current label mappings live in a `VALUE_ALIASES` table. Re-capture the relevant control from DevTools and adjust the matcher; the floating note's per-field status (`filled ✓` / `not found ✗`) shows exactly what worked.
