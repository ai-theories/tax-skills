---
name: salt-review
description: Compute the federal cap on deducting state and local tax, including its phase-down, and report what this deployment can say about an individual state's own tax. No state's own income tax is computed in this release.
---

# State and local tax review

## When to use this

Someone asks how much of their state and local tax is deductible, or how the
cap changes as income rises.

Users often say "state tax" when they mean the federal deduction for it, and
the reverse. Establish which they mean before answering.

## Call

`calculate_salt_cap` on `taxagent-salt`. You need the tax paid, modified AGI
and filing status.

Check first whether the return itemizes at all. For many taxpayers the
standard deduction wins and the cap never bites, so quoting a cap figure would
be answering a question they do not have.

## What to explain

The cap does not switch off at the threshold. It falls 30 cents per dollar of
MAGI above it and stops at a floor. Treating it as a cliff overstates the
deduction just below the threshold and understates it well above.

Say the consequence without being asked: inside the phase-down range, an extra
dollar of income costs more than its own marginal rate, because it also shrinks
the deduction. That changes the arithmetic of realising a gain, exercising
options or taking a bonus in that band.

Tax above the cap is simply lost. It does not carry forward.

## What you refuse

No state's own income tax is computed here. Use `calculate_state_coverage` on
`taxagent-salt` to say so precisely rather than answering from memory.

If the user offers you their state's rate, do not take it. A rate without that
state's base, its treatment of federal deductions and its capital gain rules
is not a calculation, and a figure produced that way would be indistinguishable
from a verified one.

Residency, domicile, part-year allocation and reciprocity are not modelled. A
client who moved during the year has a question this deployment cannot answer.

Mention that many states offer a pass-through entity tax that is deductible
above this cap — not mentioning it leaves a capped client worse off — but do
not say whether their state has one.
