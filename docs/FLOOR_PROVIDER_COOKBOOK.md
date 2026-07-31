# Floor Provider Cookbook

## Generate the reference provider

```bash
python -m earcrate floor scaffold build/reference-floor-provider
```

The generated provider uses only Python's standard library. Its directory can be
copied to another machine without changing the manifest identity.

## Run once

```bash
python -m earcrate floor invoke \
  build/reference-floor-provider/reference.floor-provider.json \
  build/reference-floor-provider/request.json \
  build/reference-floor-run
```

Outputs:

```text
build/reference-floor-run/
  artifacts/echo.txt
  result.json
  invocation.receipt.json
```

## Run conformance twice

```bash
python -m earcrate floor conformance \
  build/reference-floor-provider/reference.floor-provider.json \
  build/reference-floor-provider/request.json \
  build/reference-floor-conformance \
  --repeat 2
```

## Discover providers

```bash
python -m earcrate floor catalog ./providers ./vendor
python -m earcrate floor catalog ./providers --request request.json
```

## Write schemas

```bash
python -m earcrate floor schemas build/floor-schemas
```

## Export a portable crate

```bash
python -m earcrate floor crate \
  provider.manifest.json \
  request.json \
  result.json \
  invocation.receipt.json \
  build/floor-crate
```

Add verified derived files explicitly:

```bash
python -m earcrate floor crate \
  provider.manifest.json request.json result.json invocation.receipt.json \
  build/floor-crate-with-derived \
  --artifact-root build/reference-floor-run/artifacts \
  --copy-derived
```

Source inputs are not copied.

## Implement in another language

A Rust, C++, Go, JavaScript, Java, container, ONNX, Vamp, or CLAP adapter needs
only to implement the wire contract:

```text
read request JSON from stdin
write diagnostics to stderr
write derived files below FLOOR_ARTIFACT_DIR
write one result JSON to stdout
```

The host owns sealing and custody. The provider never needs EarCrate's Python
classes.
