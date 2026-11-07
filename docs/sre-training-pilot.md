# SRE training pilot: data preparation before model training

The question is whether lifecycle-verified operational trajectories can help train
an agent that generalizes beyond the incidents it saw. The initial implementation
is a local curation and ATIF export workflow, not a training claim.

## What the existing data establishes

The broader managed-session inventory contains 1,032 distinct source artifacts,
31 model IDs, 15 scenario IDs, and 872 lifecycle-verified passes. It includes
repeated attempts, scenario versions, and different conditions; these totals are
an inventory, not a pooled benchmark score. Legacy runs outside the managed-session
directory were not included. No training rights have been approved.

The first inventory covers the three audited frozen Core batches for Crouton Alpha,
Ripple-65B-Preview, and Grok 4.7: 99 attempts, 11 distinct scenarios, 83 scored
lifecycle-verified passes. It preserves failed and unavailable attempts for analysis.
Zero examples have approved training rights or completed privacy/quality review.
One successful Nginx trajectory has a private review copy prepared; Grok's reboot
failure has a rejection annotation and failure tag. No model training or additional
paid inference occurred.

The current working scenario-pack checkout contains 20 scenario manifests, while
the frozen Core cohort used for those runs selects 11. Other branches/experiments
may contain additional incidents; this count is not a claim that only 20 exist in
all worktrees. Existing scenario-pack and benchmark version conventions should
continue to govern new content.

Eleven incidents with repeated attempts are useful for comparing configurations
on that defined suite. They are much less diverse than 99 independent incidents.
More tokens, models, or attempts do not automatically add operational diversity.
Do not split attempts or model runs of the same incident between train and test.

## What to add next

Keep frozen Core stable for comparability. Add training/development incidents to a
separate versioned scenario pack or manifest, with family labels and held-out
variants defined before collecting training examples. Use the existing scenario
format and independent verifier; no new generator framework is required.

Prioritize causally different incidents, not renamed services or changed ports:

| Family | Distinct causes worth covering | Observable acceptance checks |
| --- | --- | --- |
| DNS/service discovery | wrong resolver; stale local override; split-horizon mismatch | intended names resolve, unrelated resolution preserved, survives restart/reboot |
| Storage/filesystem | full blocks; exhausted inodes; wrong ownership or mount options | real writes and existing data preserved, bounded free capacity restored, persistence |
| Kubernetes networking | wrong service selector; blocked network policy; broken name resolution | independent client reaches intended workload, isolation preserved, workload restart |
| Deployment/data compatibility | incompatible config; schema skew; partial rollout | old/new clients work, data invariants hold, restart and rollback behavior verified |

Include cases where the correct response is to gather missing evidence or escalate,
but only after defining executable acceptance criteria for those outcomes. Do not
reward arbitrary edits simply because a service responds again. Every new scoring
scenario still needs an oracle and fresh-instance, restart and reboot validation.
Kubernetes scenarios need an appropriate durability contract rather than pretending
a pod restart is equivalent to a host reboot.

## A small later experiment

1. Review rights before selecting model-produced examples for training. Public
   code does not settle the status of outputs. Gather permissioned/self-generated
   alternatives if existing outputs cannot be used.
2. Manually curate a small set of efficient, defensible successful repairs. Keep
   original evidence and distinguish redactions from original actions.
3. Declare train/validation/test families in advance. Hold out new causal variants
   and at least one entire family; do not tune against that test set.
4. Fine-tune one small model only after a compute budget and training recipe are
   chosen. The current prototype does not select a model, buy compute, or train.
5. Compare the unchanged and trained models with identical tools, budgets and
   independent acceptance checks. Report repair success, durability, time, cost,
   tool behavior, dataset size, and manual interventions, including negative results.

A gain on these held-out tasks would support another experiment, not broad
superiority. Family grouping and exact-duplicate checks reduce obvious leakage;
they do not establish that the test incidents were absent from pretraining.
