# Flim community-symbolic witness

`flim_bad_plus_v1` occupies an explicit middle tier in EarCrate's evidence ladder:

```text
authoritative score control
        ↓
community-symbolic witness
        ↓
blind recording inference
        ↓
sealed cross-modal acceptance
```

The supplied report describes a target-conditioned reconstruction of The Bad Plus's performance of Aphex Twin's **Flim**, built from catalog identity plus public community notation, instrument parts, a drum-pattern recipe, and performance commentary. It reports an editable 56-bar trio witness, a new 16-bar continuation, and a six-operation MixScore handoff. It also states that the target recording bytes, Basic Pitch, and the cephalopod reader were not used.

EarCrate therefore preserves two truths at once:

1. the package can prove a useful community-symbolic reconstruction, composition, and source-transport circulation;
2. it cannot prove blind listening or cross-modal convergence.

## Exact custody

The external compact pack is bound by SHA-256:

```text
a7dabd71af884a4933b7e3c8077bc9d5e7b2e69de3fa9d370fd8b592d09cdf52
```

The importer refuses another archive, unsafe ZIP paths, symlinks, duplicate members, and missing required proof members. Repository data contains the manifest, reported metrics, evidence-tier declaration, schemas, and receipts—not the copyrighted source material.

## Commands

```bash
python -m earcrate buffalo flim-report

python -m earcrate buffalo flim-import \
  /path/to/flim_bad_plus_proof_compact.zip \
  build/flim_bad_plus.pack.receipt.json
```

`flim-report` validates the repository-managed contract. `flim-import` additionally binds the exact external pack and emits a receipt that passes the community-symbolic witness, symbolic harmony, adjacent-move, and MixScore handoff organs while leaving blind audio inference and whole-organism passage blocked.

## Reported specimen facts

```text
Target witness:        56 bars / 224 beats / 2,012 MIDI note-ons
Witness roles:         piano 638 / acoustic bass 222 / drums 1,152
Witness duration:      99.339 s
Continuation:          16 bars / 538 MIDI note-ons / 15 transition proofs
Transport:             6 selected / 6 executed / 0 refused
Stem reconciliation:   0.0 for witness, continuation, and transport
```

These are report-derived facts until the exact compact pack is supplied and bound. Even after binding, the receipt describes exact package custody plus a machine-checked report contract; it does not silently upgrade the package to independently re-executed blind-audio evidence.
