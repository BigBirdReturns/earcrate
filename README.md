# EarCrate

EarCrate is currently building **Album One**, an append-only seven-track
commission that turns the project's reference reconstructions, cross-song
transplants, performance studies, and perceptual discoveries into one record.

Current truth:

- **0/7** album masters are owner-accepted.
- **0/7** references are completed by the system.
- **A1-07, Beggin' × Beggin'**, is the active proving track.
- Provider execution, green gates, reproducibility, and signal sanity do not
  count as musical acceptance.

Start with [`ALBUM_ONE.md`](ALBUM_ONE.md). The sealed machine-readable ledger is
[`configs/album_one/manifest.v1.json`](configs/album_one/manifest.v1.json).
Issue [#73](https://github.com/BigBirdReturns/earcrate/issues/73) tracks the
album-completion program.

## The album

| ID | Working title | Current boundary |
| --- | --- | --- |
| A1-01 | Pretty Lights, “Empire State of Mind” | Reproducible candidate exists; owner acceptance remains open |
| A1-02 | Robert Miles, “Children” | Score authority is bound; exact audio convergence remains open |
| A1-03 | Aphex Twin / The Bad Plus, “Flim” | Community-symbolic authority is bound; blind audio convergence remains open |
| A1-04 | KATSEYE “Animal” × Britney Spears “Toxic” | Exact private source binding and owner audition remain open |
| A1-05 | sombr “My Body Isn’t Ready” × Coldplay “Yellow” | Exact private source binding and owner audition remain open |
| A1-06 | PinkPantheress “Stateside” / bhangra gesture | Canonical gesture specimen and comparison corpus remain open |
| A1-07 | The Four Seasons “Beggin’” × Måneskin “Beggin’” | Active; deterministic diagnostic exists, accepted gold performance does not |

## What the repository is for

EarCrate is a local-first music system that turns exact private source custody,
MIR and ML observations, score and MIDI evidence, approved library material,
deterministic rendering, and owner review into reproducible musical projects.
The organs are replaceable. The album tracks and their acceptance contracts are
the product authority.

Every change must name an Album One track or a demonstrated album-wide bottleneck,
the musical gap it closes, the control it must beat, and the owner audition it
improves. A newly installed model is not progress until it changes a track-level
listening decision.

The active stack is organized accordingly:

- PR #70 supplies the Homelab organ factory.
- PR #71 supplies Reference Zero performance authority for A1-07.
- PR #72 admits generative providers as low-authority material organs.
- This Album One program defines the seven musical deliverables those systems
  must serve.

## Run

- Prerequisite: Python 3 plus `ffmpeg` and `ffprobe` on `PATH`.
- Windows first run: `START_HERE.cmd`
- Windows normal launch: `Launch-EarCrate.cmd`
- Development: `python -m earcrate`
- Complete gates: `python tests/run_gates.py`
- Package verification: `python VERIFY_PACKAGE.py`

The previous infrastructure-oriented README is retained verbatim at
[`docs/TECHNICAL_README.md`](docs/TECHNICAL_README.md).

## Privacy and authority

Source recordings, stems, audition audio, prompts, lyrics, model weights,
credentials, local paths, private option maps, and review authority remain local.
The repository carries contracts, identities, code, schemas, and source-free
receipts. Human musical acceptance and rights eligibility remain separate gates.

## License

PolyForm Noncommercial 1.0.0. Software rights do not confer rights in music or
recordings. See [`LICENSE.md`](LICENSE.md).
