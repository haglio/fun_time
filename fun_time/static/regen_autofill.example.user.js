// ==UserScript==
// @name         Fun Time — provider prompt autofill (example)
// @namespace    fun_time
// @version      1.0
// @description  Sanitized template. When Fun Time opens a generation provider's page with a #ft= payload, this userscript fills the prompt/seed/settings so a favourite can be regenerated. The real, provider-specific script (its selectors and @match host) lives in the git-ignored regen_autofill.user.js next to this file; copy this template there and adapt it to your provider.
// @updateURL    http://127.0.0.1:8770/regen_autofill.user.js
// @downloadURL  http://127.0.0.1:8770/regen_autofill.user.js
// @match        https://example.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * Fun Time packs a locked video's metadata into the URL fragment as
 *   #ft=<uriComponent(JSON)>
 * with shape:
 *   { kind: "image"|"video", positive, negative, settings: [[label,val]...],
 *     video_prompt, video_settings: [[label,val]...] }
 *
 * This example only reads the payload and pins a copy-able note listing the
 * fields, so a fresh/public checkout has a working, harmless script. The real
 * script additionally drives the provider's generate form (fill fields, set the
 * seed, pick settings, etc.); those selectors are provider-specific and are kept
 * out of the public repo.
 */
(function () {
  "use strict";

  function readPayload() {
    const m = /[#&]ft=([^&]+)/.exec(location.hash || "");
    if (!m) return null;
    try {
      return JSON.parse(decodeURIComponent(m[1]));
    } catch (e) {
      return null;
    }
  }

  function pinNote(payload) {
    const note = document.createElement("pre");
    note.style.cssText =
      "position:fixed;top:8px;right:8px;z-index:99999;max-width:40ch;" +
      "padding:8px;background:#000;color:#0f0;font:12px/1.4 monospace;" +
      "white-space:pre-wrap;border:1px solid #0f0;";
    note.textContent =
      "Fun Time regen payload:\n" + JSON.stringify(payload, null, 2);
    document.body.appendChild(note);
  }

  const payload = readPayload();
  if (payload) pinNote(payload);
})();
