# Random Favs Browser

Fun Time can open a Chrome window behind MFP on the left side of the secondary monitor.

How it works:

- Fun Time reads `Fun Time Favs` from Chrome bookmarks for the `Blair` profile.
- At launch, it picks a random subset of 10 bookmark URLs.
- It launches the project-local shortcut `Blair Chrome.lnk`.
- It waits for the new Chrome window, sizes it to the full left third of the secondary monitor, and leaves MFP on top.
- `Ctrl+Alt+Q` closes the RFB Chrome window gracefully (sends WM_CLOSE to the captured hwnd).

Current setup:

- Shortcut path: `Blair Chrome.lnk`
- Chrome profile: `Profile 2` / visible name `Blair`
- Bookmark folder: `Fun Time Favs`

Configuration lives in `fun_time_config.json` under `random_favs_browser`.

## Provider prompt autofill (on lock)

When you lock an AI video that came from Provider, Fun Time opens a Provider **generate** page (instead of the now-dead gallery link) with the original prompts/settings packed into the URL fragment (`#ft=…`). A userscript fills the form:

- **No source image** (text-to-video) → opens `example.com/video` and fills the video prompt.
- **From a source image** → opens `example.com/create`, fills the positive + negative image prompts, and pins a floating note with the **video prompt** and original settings — so you regenerate the image first, then make the video from it.

Settings (model, resolution, aspect, quality, style, creativity, seed) are listed in the floating note for manual entry: Provider changed its setting options since these were made, so they are not auto-selected.

The prompts/settings come from per-video metadata JSON mirrored under `provider_regen.metadata_root`. Paths and the two generate URLs are configured in `fun_time_config.json` under `provider_regen`. Videos without a metadata sidecar fall back to the old gallery-link behavior.

### One-time setup: install the userscript

1. Install a userscript manager in the **Blair** Chrome profile (e.g. Tampermonkey).
2. Open `fun_time/static/provider_autofill.user.js` and add it as a new userscript (Tampermonkey → Create new / Import).
3. Done — locking a Provider video now opens the prefilled generate page.

The userscript only acts when the URL carries a `#ft=` fragment (i.e. opened by Fun Time); normal Provider browsing is unaffected.
