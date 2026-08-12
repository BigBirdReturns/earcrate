# A1-07 gold-v7 iteration

The sealed owner result is operational evidence, not a request for another
diagnostic interview. Gold-v6 beat literal co-play and Reference Zero v5 was
rejected. Gold-v6 therefore becomes the protected incumbent while musical
acceptance remains false.

Contract identity:

```text
b53de69559415574e77ad2eb12e1952edf66b3de780d18faed5ab7ab84f3a0cd
```

Parent owner-review receipt:

```text
96aab3a610f0786047bfa076030531ea72da4d3c2b0000e35471ac5511ddc4b3
```

## What is locked

The next pass preserves the winning causal structure. Frankie remains one
continuous, unpitched, unstretched performance through the inherited core. The
Måneskin band follows Frankie's measured bars through the same-work occurrence
map. The Estate may not restore a global click, chop Frankie into phrases, launch
another provider sweep, generate a duplicate lead, or rewrite unrelated parts of
the arrangement.

Gold-v6 remains audible and immutable. A child that fails does not erase it. A
child that wins becomes the next protected incumbent but does not become an
accepted album master without an explicit owner acceptance receipt.

## The three children

### gold-v7-production

Run this first because it changes no musical decision. Keep every source clip,
cue, duration map, pitch decision, and ownership decision identical to gold-v6.
Change only production integration: measured spectral space around Frankie,
low-end allocation, transient containment, shared room, and conservative output
gain. Preserve dynamic contrast.

This child answers whether the remaining distance is primarily mix integration.

### gold-v7-interplay

Keep the gold-v6 core and Frankie timing locked. Add no more than two explicit
cross-era ownership events, using only material already in custody. Suitable
events include an original-percussion or backing-response answer and one
Måneskin fill or takeover at a formal boundary. All changes must live inside
declared masks totalling no more than six seconds. Decoded PCM outside those
masks must match the incumbent.

This child answers whether two purposeful handoffs satisfy the original brief
without destroying the coherence that already won.

### gold-v7-arc

Wrap the exact gold-v6 render as a locked compound clip. Build a source-led
dramatic approach around it using the same-work section map and the same
Frankie-led bar conduction law. The finished passage must remain between 38 and
62 seconds and may add no more than two sections. The embedded gold-v6 core must
remain sample-identical.

This child answers whether the winning payoff becomes meaningful when the
listener receives enough build and release to enjoy it.

## Local Estate entrypoint

Use a clean worktree stacked on the renderer-boundary fix:

```powershell
cd D:\Projects\Products\EarCrate

git fetch origin agent/a1-07-gold-v7-iteration

git worktree add `
  worktrees\a1-07-gold-v7-iteration-20260812 `
  origin/agent/a1-07-gold-v7-iteration

cd worktrees\a1-07-gold-v7-iteration-20260812
```

Verify the exact parent review and create the private campaign workspace:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_A1_07_GOLD_V7.ps1 `
  -Workspace "S:\Temp\EarCrate\album-one\a1-07-gold-v7" `
  -ParentReviewReceipt `
    "S:\Temp\EarCrate\album-one\a1-07-gold-v6\owner-review-001\private\owner-review.receipt.json"
```

The command refuses the wrong parent receipt, verifies the sealed contract, and
creates one immutable incumbent area plus three child workspaces. The generated
`NEXT_ACTIONS.md` is the local execution order. Each child is rendered through
the existing Reference Zero renderer and must reproduce identical canonical PCM
twice.

## Machine admission

Every child must preserve exact source custody, account for all clips and
transforms, pass source-mutation and signal gates, preserve Frankie, and produce
audio distinct from the incumbent and the other children.

The production child must keep the clip and transform graph structurally
identical to gold-v6. The interplay child must match the incumbent outside its
declared masks. The arc child must contain the exact gold-v6 PCM as its locked
core.

At least two children must machine-qualify. Below that threshold the Estate
returns a failure ledger and no owner files.

## Owner frontier

The next owner frontier contains gold-v6 plus the machine-qualified children,
with at most four files total. Literal co-play and Reference Zero v5 have already
served their purpose and do not consume another listening slot.

The incumbent is the control. A child advances only by beating gold-v6. A
least-bad ranking is insufficient, and relative preference still does not count
as album acceptance.

## Return

Complete the private `RETURN.private.json`, then verify it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File scripts\RUN_A1_07_GOLD_V7.ps1 `
  -Workspace "S:\Temp\EarCrate\album-one\a1-07-gold-v7" `
  -VerifyReturn `
  -ReturnLedger `
    "S:\Temp\EarCrate\album-one\a1-07-gold-v7\RETURN.private.json"
```

The returned source-free projection must report the exact branch head, parent
receipt, parent score and PCM identities, every child score and PCM identity,
reproduction receipts, machine states, masks, qualified count, and whether an
owner frontier was created. Source audio, private paths, prompts, option maps,
and review authority remain local.
