# EarCrate Homelab operations

This runbook covers the parts that usually remain implicit until after launch:
state durability, worker recovery, private review handling, backups, public
exports, upgrades, incident response, and retirement of a provider or node.

The Homelab remains a local control plane. It does not install providers, download
weights, invoke network services, decode source recordings during audit, or turn a
successful process exit into acceptance.

## Authority and storage

```text
sealed JSON object
    durable evidence authority

SQLite database
    index, dependency scheduler, lease manager, event journal

source recordings / private library / model weights
    external or estate-managed bytes, never embedded in the Homelab executable
```

A Homelab object is identified by its semantic seal. The store separately records
the raw JSON file hash and size. `store-doctor` verifies both.

Default store shape:

```text
<store>/
  db/homelab.sqlite3
  objects/public/<kind>/<prefix>/<identity>.json
  objects/private/<kind>/<prefix>/<identity>.json
  objects/sensitive/<kind>/<prefix>/<identity>.json
```

Private and sensitive objects require explicit access. They are excluded from
public export.

## Initial deployment

1. Create an estate survey and Homelab audit without executing providers.
2. Initialize a new durable store.
3. Ingest the catalog, node receipt, inventory, audit, and campaign.
4. Run the doctor.
5. Create a private backup before the first experiment.

```bash
python -m earcrate homelab sweep \
  --root <repo-or-workspace-root> \
  --root <model-root> \
  --root <proof-and-audition-root> \
  --estate-root <managed-estate-root> \
  --output-dir <new-survey-directory> \
  --audio-devices

python -m earcrate homelab store-init <store>
python -m earcrate homelab store-ingest <store> <survey>/homelab.catalog.json
python -m earcrate homelab store-ingest <store> <survey>/homelab.node.json
python -m earcrate homelab store-ingest <store> <survey>/homelab.audit.json
python -m earcrate homelab store-ingest <store> <survey>/homelab.campaign.json
python -m earcrate homelab campaign-register <store> <survey>/homelab.campaign.json
python -m earcrate homelab store-doctor <store>
```

The survey output directory must be new or empty. Scanned roots are unchanged.

## Worker protocol

Workers lease only dependency-ready tasks.

```bash
python -m earcrate homelab task-lease <store> <worker-id> \
  --resource gpu-exclusive \
  --lease-seconds 1800 \
  --token-output <private-token-file>
```

The worker must:

1. preserve the task and node identities;
2. keep the lease token private;
3. heartbeat before lease expiry;
4. write stage artifacts outside the store first;
5. content-address every output;
6. seal and ingest the resulting receipt;
7. complete the task with that ingested receipt identity.

```bash
python -m earcrate homelab task-heartbeat \
  <store> <campaign-sha> <task-id> <private-token-file>

python -m earcrate homelab store-ingest <store> <stage-receipt.json>

python -m earcrate homelab task-complete \
  <store> <campaign-sha> <task-id> <private-token-file> completed \
  --evidence <receipt-sha256>
```

Failed work is retried with bounded exponential backoff until `max_attempts` is
reached. An expired lease is recovered by the next scheduler transaction. A task
without an ingested evidence object cannot be completed successfully.

GPU and physical-audio-device tasks should use exclusive resource classes. Do not
run two quality trials on the same exclusive device unless the test policy
explicitly permits contention.

## Changed prerequisites

A blocked campaign is historical evidence; do not mutate its catalog, target
manifests, or audit in place.

After installing software, binding a fixture, changing a node, or adding model
weights:

1. capture a new rig/node receipt;
2. rerun estate inventory and Homelab audit;
3. generate a new campaign;
4. cancel the obsolete active campaign with a reason;
5. register the new campaign.

```bash
python -m earcrate homelab campaign-cancel \
  <store> <old-campaign-sha> "superseded by refreshed node and inventory"
```

This preserves the failed or blocked attempt instead of rewriting it.

## Blind review protocol

The operator and reviewer receive different material.

Operator:

```bash
python -m earcrate homelab review-prepare \
  <catalog.json> <target-id> <node-sha> <reviewer-id> \
  <candidate-audio> <control-audio> \
  --fixture <fixture-id> \
  --playback-json '{"device":"...","sample_rate":48000,"level":"matched"}' \
  --public-dir <new-public-directory> \
  --private-dir <new-private-directory>
```

The public directory contains only opaque A/B files, their exact identities,
checksums, the playback contract, and the public assignment. The private directory
contains the option map and review token. Never place the private directory under
the public directory or send it to the reviewer.

Reviewer:

```bash
python -m earcrate homelab review-submit \
  <public-assignment.json> <reviewer-id> <private-review-token-file> A \
  --dimensions-json '{"bleed":4,"transients":5,"role_usefulness":5}' \
  --output <submission.json>
```

Adjudicator:

```bash
python -m earcrate homelab review-adjudicate \
  <catalog.json> <public-assignment.json> \
  <private-assignment-authority.json> <submission.json> \
  --output <audition-ledger.json>
```

The direct `record-audition` command exists for already adjudicated or non-blind
review evidence. It is not a shortcut around committed A/B assignment for a target
whose required stage is `blind_audition`.

## Adoption decisions

An accepted target requires:

```text
current catalog and target manifest
assigned node identity
all required nonterminal stages passed
all required fixtures covered
no current failed or refused stage
accepting human audition where required
supporting receipt identities present in the audited estate
explicit deciding authority and reason
```

A target decision is scoped. It is not legal clearance, release approval, or
whole-Buffalo passage. Rejected, deferred, and reference-only targets remain in
the catalog and store.

## Doctor and incident response

Run `store-doctor`:

- before and after a campaign;
- before public export;
- before backup;
- after restore;
- after an unclean shutdown;
- after filesystem, antivirus, backup-agent, or disk errors.

```bash
python -m earcrate homelab store-doctor <store>
```

Doctor verifies:

```text
SQLite quick_check
store schema version
hash-chained event journal
indexed object bytes and semantic seals
unindexed object files
task dependency references
lease-field consistency
expired leases
task evidence references
```

Do not delete a mismatched object or edit the database manually. Preserve the
store, copy the doctor output, restore the newest verified backup into a new
location, and compare the two stores by object identity.

## Backup and restore

A full backup includes private review mappings and sensitive objects when present.
It is integrity protected but not internally encrypted. Store it only on encrypted
media or inside an encrypted backup system.

```bash
python -m earcrate homelab backup \
  <store> <new-backup.zip> \
  --acknowledge-private-state
```

Restore requires the exact backup ZIP SHA and a destination that does not exist.

```bash
python -m earcrate homelab restore \
  <backup.zip> <new-store-directory> \
  --approve <backup-sha256> \
  --output <restore-receipt.json>
```

Restore refuses path traversal, duplicate members, symlinks, undeclared files,
size-budget violations, checksum mismatches, unsupported store schemas, and a
failed post-restore doctor. Promotion to the final destination is atomic.

Recommended policy:

```text
before first campaign             one full backup
before catalog/schema upgrade     one full backup
before deleting a node/store      one full backup
weekly while campaigns are active one full backup
retention                          latest 4 weekly + every pre-upgrade backup
restore drill                      at least quarterly
```

## Public export and dashboard

Public export contains only public sealed objects and a source-free store
snapshot. It excludes private/sensitive objects, source media, model weights,
credentials, and absolute paths.

```bash
python -m earcrate homelab public-export <store> <new-export-directory>
python -m earcrate homelab dashboard <audit.json> <campaign.json> <dashboard.html>
```

An existing nonempty export or dashboard destination is refused rather than
overwritten.

## Upgrade policy

Before changing the Homelab schema, catalog, scheduler, or store format:

1. pass the current doctor;
2. create a verified backup;
3. preserve the previous catalog and schemas;
4. add an explicit migration or refuse the old version;
5. test upgrade and restore on Linux and Windows;
6. prove deterministic zipapp construction;
7. verify that old receipts become stale rather than silently current;
8. update the operations runbook and failure recovery instructions.

No code change may silently rewrite an existing sealed object.

## Node retirement

Before retiring or replacing a machine:

1. allow active tasks to complete or cancel their campaign;
2. capture a final node receipt;
3. run doctor;
4. create and restore-test a backup;
5. retain accepted provider receipts and node-scoped failure evidence;
6. mark future campaigns against the replacement node identity.

The old node remains evidence. It is not an active execution target after
retirement.

## Launch gate

The Homelab infrastructure is launchable only when:

```text
all focused tests pass on Linux and Windows
Python 3.11 and 3.12 pass
complete EarCrate gates pass
package verifier passes
deterministic Homelab zipapp builds twice byte-identically
87 targets and 10 fixtures remain catalogued
store-init and doctor pass from the zipapp
backup and restore pass against private state
blind review proves public/private separation
public export proves private-state exclusion
PR description matches the exact head and executable scope
```

Passing this launch gate does not mean any commodity provider has passed its real
fixture or human audition. It means the machinery is safe enough to begin those
trials without losing their evidence.
