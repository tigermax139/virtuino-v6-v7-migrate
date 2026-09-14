# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python 3 script (`migrate.py`, no dependencies, no test suite, no build step) that converts a Virtuino 6 `.vrt6` project into a Virtuino IoT `.vrt7` project. Both file formats are SQLite databases; the whole job is reading one schema and writing another.

## Running it

```bash
python3 migrate.py old_project.vrt6 --template template.vrt7 -o migrated.vrt7
```

`--template` is mandatory and not optional-by-oversight: `.vrt7` stores the server IP AES-encrypted inside `connection.extraData.serverUrl`, and the app's key is not extracted. The migrator therefore copies the template file and edits it in place, cloning the template's encrypted IP, its Modbus connection row, and its button/label widget rows as style prototypes. The user re-types IPs in the app after import.

Verification is manual — there are no automated tests. Generate an output file and inspect it with `sqlite3`, and check the `PRAGMA integrity_check` line the script prints.

## Architecture

`read_vrt6()` → dict of `servers` / `panels` / `switches`, then `migrate()` writes them into a copy of the template. Two non-obvious pieces:

- **Label matching.** Virtuino 6 stores text labels as separate widgets in a `text` table, unrelated to the switch. `read_vrt6()` pairs each switch with the geometrically nearest label on the same panel (`math.hypot` over x/y) and stores it as `sw["label"]`.
- **Template cloning by column list.** Every insert reads `list(row.keys())` from the prototype row and builds the INSERT from it, so the script survives schema columns it does not know about. Follow this pattern rather than writing explicit column lists.

### Invariants when writing `.vrt7`

These are easy to break and produce a file the app silently mishandles:

- Adding a connection means also creating `variable_<newid>` and `virtuino_value_<newid>` tables (DDL cloned from the template's pair via string replace) and inserting all `VAR_POOL_SIZE` (500) default rows. The app expects the pool to exist.
- Variables reference a Modbus unit by the unit's `uniqueID` (a millisecond-timestamp-like number in `unitListData`), **not** by its `unitID`.
- `sqlite_sequence` must be updated for every table inserted into, or the app reuses IDs.
- Widget geometry is redundant: `left/top/width/height` **and** `right/bottom` must agree.
- vrt6's `servers.serverExtra.unitList` is JSON-inside-JSON (double-encoded); it is parsed defensively because some projects store it as a string and some as a list.
- Modbus TCP is `serverType=300` in vrt7; coils are `functionID=0`. Both are module constants.

## Format reference

`docs/FORMAT.md` holds the reverse-engineered schema notes for both formats — table meanings, `extraData` JSON fields, the `actionsListData` action codes, colour encoding. Read it before touching any schema assumption, and update it when new fields are decoded; it is the only public documentation of these formats.

## Scope

Only coil-type digital-output switches migrate. Charts, gauges, sliders, analog registers and MQTT connections are unimplemented — not blocked, just not written yet. Tested against vrt6 `user_version=102` and vrt7 `user_version=23`; other app versions may differ.

## Safety

Real `.vrt6`/`.vrt7` files are gitignored — they contain home network layouts and PLC addresses. Never commit one, and never paste IPs or coil maps from a user's project into the repo. Migrated widgets write to real PLC coils, so preserve the "test one switch first" warning the script prints.
