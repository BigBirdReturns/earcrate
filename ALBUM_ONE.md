# Album One

Album One is EarCrate's primary musical program. The repository exists to complete
these seven commissioned works, assemble them into one coherent record, and turn
the mechanisms that repeatedly succeed into the reusable EarCrate floor. Provider
catalogs, GPU orchestration, schemas, receipts, score readers, stem systems, MIDI
engines, generative models, and review machinery are subordinate to this program.

The commission ledger is append-only. Its current order records when each work
entered the project; it is not the final album sequence. Final sequencing begins
only after the tracks exist as owner-accepted music.

## Current ledger

- Album masters accepted: **1/7**
- System references completed: **0/7**
- Active proving track: **A1-07, Beggin' × Beggin'** (master accepted; system
  reference open)
- Final album sequence: **unsequenced**
- Acceptance authority: **the owner**
- Release authority: **a separate rights decision**

The machine-readable authority is
[`configs/album_one/manifest.v1.json`](configs/album_one/manifest.v1.json).
Its schema is
[`schemas/earcrate_album_program_v1.schema.json`](schemas/earcrate_album_program_v1.schema.json).
The manifest seal is
`35df1f752a3622008d4ec2d76d2f389a1f88d5e546ef9d28fce5a1fbc4955696`.

## The seven commissioned tracks

| ID | Working title | Reference class | Evidence now | Next musical gate |
| --- | --- | --- | --- | --- |
| **A1-01** | Pretty Lights, “Empire State of Mind” | Reference-fidelity reconstruction | Reproducible release-candidate fixture; signal sane; human acceptance pending | Put the strongest retained candidate and controls before the owner and require a musical verdict |
| **A1-02** | Robert Miles, “Children” | Cross-modal score and audio reconstruction | Score, annotations, MIDI, MixScore, and score-side proof are bound; exact audio remains unbound | Bind the exact recording and make score, MIDI, racks, and audio converge on one performance |
| **A1-03** | Aphex Twin / The Bad Plus, “Flim” | Performance-arrangement reconstruction | Community-symbolic witness and adjacent-move evidence are bound; blind audio remains unbound | Bind the exact performance and require symbolic and audio convergence before audition |
| **A1-04** | KATSEYE “Animal” × Britney Spears “Toxic” | Production-grammar transplant | Normalized specimen exists; exact private editions remain unbound | Test role-based percussion and identity transfer against naive overlap |
| **A1-05** | sombr “My Body Isn’t Ready” × Coldplay “Yellow” | Arrangement-ancestry handoff | Normalized specimen exists; exact private editions remain unbound | Test chorus width, drum lift, neutral alignment, and reverse modernization under blind review |
| **A1-06** | PinkPantheress “Stateside” / bhangra gesture | Perceptual gesture to arrangement | Commissioned, but no canonical specimen or comparison corpus is materialized | Isolate the gesture and build a held-out retrieval and transformation audition with negative controls |
| **A1-07** | The Four Seasons “Beggin’” × Måneskin “Beggin’” | Same-work, cross-era performance arrangement | **Accepted album master**: native-pocket full form, auditioned, deterministic, exactly reproducible | Run the withheld-answer recovery challenge; the album claim is closed, the autonomy claim is not |

The answer-key corpora in `earcrate/reference/` remain calibration and discovery
graders. They are not additional album tracks and may not inflate the completion
count.

## Three different completion claims

### 1. Album master

A track has an accepted album master only when the owner accepts the music, every
selected source and transform is accounted for, and the accepted render can be
reproduced exactly from a portable score or equivalent execution object. A
signal-sane file, a deterministic file, a green provider run, or the least-bad
candidate does not satisfy this claim.

### 2. System reference completion

EarCrate completes a reference only after an accepted album master exists, the
gold decisions are withheld, and an inferred candidate blindly beats the
declared naive control. This is the autonomy claim. It is deliberately stricter
than producing an album master.

### 3. Release eligibility

Musical acceptance and system completion do not confer rights. Publication,
distribution, and commercial use require a separate rights decision. Source
recordings, stems, private prompts, lyrics, model assets, credentials, local
paths, and private option maps remain outside the repository.

## Active focus: A1-07 Beggin

Beggin is the first track to reach an **accepted album master**. It got there
through three states the lane keeps separate, because they are easy to collapse
into one:

| State | Reached by | A1-07 |
| --- | --- | --- |
| `frontier_selected` | sealed owner verdict over a blind frontier | yes |
| `master_qualified` | deterministic master pair and every signal gate | yes |
| `master_accepted` | owner verdict naming the mastered PCM itself | yes |

The qualified object is the native-pocket full form: 56.111 s of setup, body and
payoff, selected from a blind frontier whose verdict was sealed before the letter
map was revealed, ratified in the monitoring room as `ACCEPT_FOR_MASTERING`, then
mastered with a single solved linear gain of +2.5 dB to a -1.0 dBTP ceiling. No
limiter, no EQ, no multiband, no resampling and no dither, so the 8.5 LU macro span
survives exactly and two executions agree on canonical PCM and on container bytes.
The public receipt is
[`proofs/album_one/a1-07-master-v1.public.json`](proofs/album_one/a1-07-master-v1.public.json).

The counter moved only on the third state. The monitoring verdict accepted the
production render and authorized the chain; it could not accept the mastered WAV,
because that object did not exist when the verdict was given. The transform is a
linear gain of a known size, so it was tempting to treat the audition as a
formality — but that argument replaces a listening decision with an inference, and
the completion model does not allow it. The owner reconstructed the declared chain
from the accepted render, compared the two at sample level, and returned
`ACCEPT_MASTER` against the mastered PCM by identity. The acceptance receipt is
[`proofs/album_one/a1-07-master-acceptance-v1.public.json`](proofs/album_one/a1-07-master-acceptance-v1.public.json).

The withheld-answer recovery challenge is a further, separate contract, so
`system_reference` stays `incomplete` regardless of how the audition goes. And what
this lane proves is bounded: private source custody, deterministic rendering, blind
owner review, and linear mastering. It does not prove arrangement synthesis. Both
A1-07 sources are recordings, and the render places donor material rather than
realizing a performance from symbolic authority.

Everything below this paragraph describes how the track got here, and is retained
because the rejected work is the reason the accepted work is trustworthy.

The retained v5 `PerformanceScore` is useful precisely because it is honest. It
accounts for all 17 selected clips, reproduces byte-identically, and records the
exact score identity
`4c36cf3a8bd86aa4d04b71a810da5effce0bc790944366ae92b61987cd35c812`.
The two reproduced renders share canonical PCM identity
`bde6ab5ad2e054fb02bdc15323633c23350884fecb22d4f84d29df793b269031`.
The authority fields remain `human_acceptance=false` and
`inference_success=false`. This is a reproducible diagnostic, not an album track.

The private Beggin custody remains split across the CORE and ATTEMPTS archives.
Only their identities are recorded here:

- CORE:
  `8efa0d0352d958ccfde4fe8d852b4f976be3e034a49ca99ec3545435e72a9291`
- ATTEMPTS:
  `977ffaf2b828f4e9d5abcdbf652640682c3a8180499fd04f397d2e89e84d9db9`

The Beggin work that followed was composition, not another provider census.
The gold authoring pass must use the same-work identity, section correspondence,
material census, phrase authority, minimal-intervention prior, accepted pulse and
transform organs, and a serious naive co-play control. Generative systems may
supply bounded material or propose alternatives. They may not replace the
conductor, accept the music, or redefine the track.

## Repository operating contract

Every pull request must declare the following in its description:

- `album_scope`: one of `A1-01` through `A1-07`, `album-wide`, or
  `infrastructure-with-track-demand`;
- `musical_gap`: the exact album failure being addressed;
- `control_or_baseline`: the incumbent or naive mechanism the change must beat;
- `owner_audition_effect`: what new or improved listening decision the owner will
  receive;
- `private_execution_required`: the exact local evidence, hardware, or media still
  required.

A generic provider campaign is non-landing work unless it is attached to a named
track, a bounded hypothesis, and a downstream audition. Provider installation,
execution, benchmark passage, and machine qualification remain lower-authority
evidence. They do not become track acceptance.

A new organ should enter the repository only when it closes a declared track gap
or an album-wide bottleneck demonstrated by more than one track. The factory
should compare type-correct combinations against a track contract, preserve the
incumbent, and return a small, strategy-diverse frontier. It should not ask one
tool to solve an entire song or construct a meaningless Cartesian product across
providers.

## The current pull-request stack

The active stack now has a clear relationship to Album One:

1. **PR #70, Homelab organ factory** is album-wide infrastructure. It schedules
   and evaluates organs, but it does not define the music.
2. **PR #71, Reference Zero** is the A1-07 performance-authority and recovery
   protocol. It separates gold authoring, exact reproduction, and automatic
   recovery.
3. **PR #72, generative provider floor** supplies low-authority material organs
   for A1-07 and later tracks. It cannot become the conductor or acceptance
   authority.
4. **Album One program authority** names the seven deliverables, their current
   evidence, the active track, and the conditions under which infrastructure may
   claim progress.

Future stacks should begin from the album track and its failure, then select
organs. They should not begin from a newly discovered model and search for a
reason to use it.

## What each track is for

Seven accepted masters is not the objective. Seven masters that each exercise a
*different* section of the architecture is. A1-07 closed one vertical slice —
custody, deterministic rendering, blind review, mastering, acceptance — and it
proved exactly that slice. Repeating its shape six more times would produce a
record and teach the system nothing.

| Track | Capability it must prove | Status |
| --- | --- | --- |
| **A1-07** | donor-recording arrangement, blind review, mastering, acceptance | proved; system reference open |
| **A1-02** | score to performance: symbolic authority realized as a played object | next vertical |
| **A1-01** | reference editing, release governance, the rights boundary | retained candidate is an edit of the reference, so it cannot prove synthesis |
| **A1-03** | performed-arrangement recovery from symbolic and blind-audio witnesses | unbound; capability fixed by its commission |
| **A1-04** | exact-edition binding and production-grammar transplant | sources unbound |
| **A1-05** | arrangement-ancestry transplant under blind review | sources unbound |
| **A1-06** | perceptual gesture to arrangement; potentially generative | unmaterialized |

A1-03, A1-05 and A1-06 keep the capabilities their commissions already name. They
are not reassigned here, because a capability map written ahead of the evidence is
a plan, not a ledger.

## Execution order

Revised after A1-07 was accepted. The previous order sent the next vertical to
A1-01; that is wrong for the capability question, and the change is recorded rather
than quietly dropped.

1. Keep all seven source and intent contracts current in the manifest.
2. Extract the reusable lane machinery from A1-07, so a new track describes music
   instead of rebuilding workflow infrastructure.
3. Bind the exact *Children* reference edition, through both container and decoded
   audio identity. Stop at `edition_candidate` if the commission does not already
   name the edition unambiguously — an answer key cannot be authoritative if the
   edition was chosen after acquisition.
4. Build the A1-02 score-to-performance realization: measure and voice model, tempo
   map and rubato, note timing, dynamics, pedalling, articulation, rack selection,
   mix-event realization, then comparison against the bound recording. The goal is
   not sample-identical reconstruction. It is to find out whether the score and the
   recording encode the same performed object, and where the symbolic path loses
   the performance.
5. Run A1-07's withheld-answer recovery challenge. Until it passes, A1-07 is an
   accepted master and a valuable specimen, but not proof that a fresh operator or
   environment can reconstruct the lane without privileged knowledge.
6. Execute the remaining reference-bound tracks in the order their custody allows,
   each against the capability its commission names.
7. Sequence the accepted masters, perform album-level continuity and mastering
   review, then make the separate rights decisions.

A1-01 remains commissioned and is not deprioritized as music. It is reclassified:
its lane is editing, custody and release governance, not the proof that EarCrate can
realize a performance.

Language-directed original arrangement is a real objective and is **not** in this
list, because it is not one of these seven commissions. It belongs to a separate
append-only commission whose input is direction rather than a target recording.

This order may change when evidence changes. The append-only commission identities
may not.

## Definition of Album One complete

Album One is complete when seven owner-accepted, exactly reproducible masters
exist, the sequence is locked, album-level transitions and mastering are accepted,
and the release ledger states the rights position for every track. The system
reference count is reported beside the album-master count rather than being
silently conflated with it.

The controlling question for repository work is: **which Album One track becomes
more likely to reach an owner-accepted master because this change exists, and
what listening control will prove it?**
