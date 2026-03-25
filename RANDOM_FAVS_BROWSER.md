# Random Favs Browser

Fun Time can open a Chrome window behind MFP on the left side of the secondary monitor.

How it works:

- Fun Time reads `Fun Time Favs` from Chrome bookmarks for the `Blair` profile.
- At launch, it picks a random subset of 10 bookmark URLs.
- It launches the project-local shortcut `Blair Chrome.lnk`.
- It waits for the new Chrome window, sizes it to the full left third of the secondary monitor, and leaves MFP on top.
- `Ctrl+Alt+Q` does not close Chrome windows opened this way.

Current setup:

- Shortcut path: `Blair Chrome.lnk`
- Chrome profile: `Profile 2` / visible name `Blair`
- Bookmark folder: `Fun Time Favs`

Configuration lives in `fun_time_config.json` under `random_favs_browser`.
