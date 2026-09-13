# Development experiment profiles

These complete configurations copy the historical pinned model, sampling, 840-second
worker budget, and controls into new files. The historical configuration is unchanged.
The endpoint is the historical endpoint, not an assertion that inference is running.
No profile has been paid-tested in this milestone. Never launch without fresh admission.

| Profile | Selected mechanism | Matched comparison |
| --- | --- | --- |
| legacy.toml | Existing behavior plus correctness fixes | Control for each single-policy comparison |
| reasoning-xhigh.toml | Explicit Qwen xhigh | legacy, or reasoning-medium with only effort changed |
| reasoning-medium.toml | Explicit Qwen medium | legacy, or reasoning-xhigh |
| generation-time-aware.toml | Time-aware output allowance | legacy |
| context-history-first.toml | Dynamic state after complete exchanges | legacy |
| progress-shadow.toml | Adaptive detector, no interventions | Offline signal review only |
| progress-reminder.toml | Adaptive detector plus one deterministic reminder | progress-critic |
| progress-critic.toml | Same triggers plus one bounded critic | progress-reminder |

The last two profiles differ only in recovery policy. Both permit at most one
intervention, and neither intervenes below 180 seconds remaining. The critic receives
at most 512 output tokens and has no tools. Its time and tokens count against the
existing worker budget. Shadow candidates are not validated diagnoses of a stall.

Changing model effort, generation limits, context layout and intervention together
would confound their effects. Promote a default only after separately labeled,
counterbalanced, matched trials measure grading coverage, reward, first edit,
meaningful verification, submission, model wait and total cost.
