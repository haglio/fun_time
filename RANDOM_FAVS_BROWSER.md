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

Model, quality, aspect, creativity, and the Style/Action modal pickers are best-effort auto-selected by matching the stored value to a current option, with known label differences mapped (e.g. model "Realism" → "Semi-Realism"; creativity 7/10 → Balance/Precise; video actions "Pov Epsilon" → "Epsilon", "Eta Form" → "Eta form", "Theta Motion" → "Bouncing motion" — see `VALUE_ALIASES` in the userscript). Values with no current equivalent (aspect "2:3", the old style names) are skipped, and an opened picker is closed again if its value is gone. On video pages "Smooth video" is always forced on, and escaped newlines in prompts are converted to real line breaks. Prompt fields are filled persistently (re-applied for a few seconds) because the React form re-mounts/clears them during hydration. The negative prompt lives in a popover the script opens first (a "no/ban" icon button), and the Action modal needs a "Select" confirm click after a card is chosen (its label is "Select" + the selected count, e.g. "Select1"). The floating note shows the positive and negative prompts with copy buttons (a fallback if auto-fill ever misses) plus every original setting with a ✓ next to each one it applied; set the rest by hand. A too-short/corrupt stored prompt (some scraped sidecars hold 3-char junk like "$58") is left blank and flagged rather than filled. The image page uses inline toggles/cards (matched by text); the video page uses popover pickers for quality/aspect (click trigger, then the option), and the seed sits behind a "Use Fixed Seed" toggle that reveals an `#seed` input. The script drives all of these best-effort. Video aspect is only landscape (16:9) or portrait (9:16), so a stored ratio is mapped by width vs height (W≥H → 16:9, else 9:16).

The prompts/settings come from per-video metadata JSON mirrored under `provider_regen.metadata_root`. Paths and the two generate URLs are configured in `fun_time_config.json` under `provider_regen`. Videos without a metadata sidecar fall back to the old gallery-link behavior.

### One-time setup: install the userscript

1. Install a userscript manager in the **Blair** Chrome profile (e.g. Tampermonkey).
2. Open `fun_time/static/provider_autofill.user.js` and add it as a new userscript (Tampermonkey → Create new / Import).
3. Done — locking a Provider video now opens the prefilled generate page.

The userscript only acts when the URL carries a `#ft=` fragment (i.e. opened by Fun Time); normal Provider browsing is unaffected.
