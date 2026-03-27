@AGENTS.md

## Win32 API changes: mandatory pre-flight

Before modifying any Win32 API call (ctypes, keyboard/mouse input, window management, thread input):

1. **State the mechanism.** Write a sentence explaining WHY the approach works, citing the specific Win32 behavior it depends on (e.g., "SendInput updates the thread key state; PostMessage does not").
2. **Verify the claim.** If you are not certain the stated mechanism is correct, say so explicitly rather than guessing. Search for authoritative documentation or test empirically.
3. **Check interactions.** Identify what other components touch the same subsystem (AHK keyboard hooks, VLC's Qt event loop, thread input queues) and explain why the change won't break them.
4. **Map from symptoms.** If fixing a bug, write down the user-reported symptom, trace the execution path that produces it, and confirm the fix addresses that specific path — not a guess at a nearby path.

If you cannot complete these steps, stop and say so. Do not submit a speculative fix.

## Test permissions

Running the project's unit test suite is ALWAYS allowed. Never ask for permission to run tests. Just run them.
