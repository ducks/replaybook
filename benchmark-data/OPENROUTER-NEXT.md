# Next OpenRouter Core comparison

Research date: 2026-10-19. This is a proposed test set, not published evidence
or authorization to start a paid matrix.

Verified against https://openrouter.ai/api/v1/models. All six IDs advertised
tool support and at least one million tokens of context at lookup time.

| Exact route | Purpose |
| --- | --- |
| `anthropic/claude-haiku-5.5` | Overlap with the OpenCode Go run |
| `xiaomi/mimo-v2.6-pro` | Overlap with Go; investigate whether long runtimes recur |
| `deepseek/deepseek-v4.1-flash-1017` | Dated Flash snapshot, economical baseline |
| `inception/mercury-3` | New diffusion-model candidate |
| `mistralai/mistral-large-4-0` | Different model family, following the AoE smoke |
| `z-ai/glm-5.3-flash` | Low-cost candidate following AoE results |

Start with Haiku, DeepSeek, and Mercury; add the other three after smoke tests.
Each full Core configuration is 8 scenarios × 3 attempts = 24 trials. Three
models means 72 trials; all six means 144. Catalog support is not proof that
every route works through the pinned harness, or that it meets privacy policy.

Exclude Meta and xAI. Enforce OpenRouter's no-training provider filter before
running, preferably account-wide; optionally require zero data retention too.
Do not silently relax privacy restrictions to obtain availability. See
https://openrouter.ai/docs/guides/routing/provider-selection and
https://openrouter.ai/docs/guides/features/zdr.

Use the frozen Core 2026.10.15 conditions, 900-second deadline and three attempts,
with a separately pinned Claux release. Verify controller/scenario fingerprints
before claiming comparability with release 20261018.0.0. Agent harness/provider
differences are intentional, but mean this is a configuration comparison, not
a causal measurement of provider or model quality alone. Keep reasoning policy
explicit. Do not treat similarly named Go routes as proven identical weights.

Publish the new run as its own reviewed release and explicitly add that release
to scores.json. Matching conditions will place its cards alongside Go; attempts
and scores will remain separate. No model-specific application branches needed.
