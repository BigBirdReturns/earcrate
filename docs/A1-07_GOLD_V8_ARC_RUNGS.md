# A1-07 gold-v8 arc rungs

This descent turns the only positive v7 owner signal into executable descendants. The gold-v7 arc's quiet, source-led introduction and crescendo are locked as a positive module. Gold-v6 remains the protected core and vocal clock. The system builds two further rungs without asking the owner to design them.

## Rungs

1. `gold-v8-arc-control` reproduces the exact qualified gold-v7 arc PCM.
2. `gold-v8-arc-production` keeps the arc introduction and tail, then splices the qualified production-integrated core into the sample-identical gold-v6 window using bounded joins.
3. `gold-v8-arc-handoff` starts from the production rung and adds one mask from the qualified v7 interplay child. The mask is chosen deterministically by musical-function priority and audible PCM difference.

The build creates two blinded review lanes. The whole-arc lane tests introduction, crescendo, and payoff. The core-window lane tests production integration and the one handoff without allowing the longer introduction to dominate the comparison.

## Local execution

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_A1_07_GOLD_V8.ps1 `
  -V7Workspace "S:\Temp\EarCrate\album-one\a1-07-gold-v7" `
  -Output "S:\Temp\EarCrate\album-one\a1-07-gold-v8" `
  -OpenReviewDirectory
```

The runner discovers and verifies the exact v7 owner receipt, protected gold-v6 score and PCM, and the three qualified v7 machine receipts. It refuses changed identities, duplicate cores, non-unique embedded gold-v6 PCM, non-reproducible outputs, altered arc material outside the joins, or a handoff that changes audio outside its selected mask.

The build outputs:

```text
review/whole-arc/public/
review/core-window/public/
plans/
renders/
machine-validation.private.json
PUBLIC_PROJECTION.json
```

After listening, the local desk can seal both natural rankings without another scorecard:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_A1_07_GOLD_V8.ps1 `
  -Output "S:\Temp\EarCrate\album-one\a1-07-gold-v8" `
  -WholeRanking "B > A > C" `
  -CoreRanking "C > B > A" `
  -Note "The intro earns the payoff; keep the single fill only if it improves the core."
```

Relative preference promotes a mechanism but does not claim Album One acceptance. Full-song rendering and withheld-answer recovery remain closed.
