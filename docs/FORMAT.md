# Virtuino project file formats (reverse-engineered notes)

Both `.vrt6` (Virtuino 6) and `.vrt7` (Virtuino IoT) files are plain
**SQLite 3 databases**. You can open them with any SQLite tool or Python's
built-in `sqlite3` module. Everything below was learned by inspecting real
files; field meanings are inferred, not official.

## .vrt6 (Virtuino 6), user_version = 102

Key tables:

| table | contents |
|---|---|
| `servers` | connections; Modbus TCP has `type=10`, plus `ipAddress`, `portNumber`, `refreshTime` (plain text, not encrypted) |
| `panel` | dashboards |
| `digital_output_component` | switch widgets: `panelID`, `serverID`, `pin` (Modbus address), `pinMode`, `valueON/valueOFF`, `x`, `y` |
| `text` | text labels: `panelID`, `x`, `y`, `textValue` — labels are separate widgets, not part of the switch |
| `buttons`, `analog_*`, ... | other widget types |

Notes:

- `servers.serverExtra` is JSON; its `unitList` field is a **JSON string
  inside JSON** (double-encoded) and holds Modbus unit IDs:
  `{"unitIndex":2,"unitID":20,"name":"..."}`.
- For Siemens LOGO! 8: coil addresses 8256–8319 (0-based) map to network
  flags M1–M64 (the official LOGO! docs list them 1-based as 8257+).

## .vrt7 (Virtuino IoT), user_version = 23

Key tables:

| table | contents |
|---|---|
| `connection` | connections; Modbus TCP has `serverType=300`; config in `extraData` (JSON) |
| `panel` | dashboards; `panelType=100` normal, `200` settings; `orderID` controls list order; `isLaunchPanel` marks the start dashboard |
| `variable_<N>` | 500 pre-created variable slots for connection `_id=N` |
| `virtuino_value_<N>` | runtime values for those variables |
| `button`, `label`, ... | widgets; each references `panelID`, `connectionID`, `variableID` |
| `sqlite_sequence` | **must be updated** after manual inserts, or the app will reuse IDs |

### connection.extraData

JSON. Interesting fields:

- `serverUrl` — the IP address, **AES-encrypted and base64-encoded**. You
  cannot write a new IP without the app's key; clone an existing encrypted
  value and let the user re-type the IP in the app (the app re-encrypts it).
- `serverPort`, `timeout`, `refreshTime` — plain numbers.
- `unitListData` — JSON string with Modbus units:
  `{"nickname":"...","unitID":20,"isEnabled":1,"forPLC":0,
  "addressSizeID":0,"uniqueID":<timestamp>}`.
  Variables reference a unit by its `uniqueID`, not by `unitID`.

### variable_N rows

Row `_id` is the variable number (V0…V499). A configured Modbus variable has
`type=1` and `extraData` like:

```json
{"multiInputID":0, "unitID":<unit uniqueID>, "address":8256,
 "valueFormatID":100, "functionID":0, "multiplier":1.0,
 "refreshType":1, "refresh":5000,
 "multipleReadingType":0, "multipleReadingCount":1}
```

- `functionID=0` → coil (read FC01 / write FC05). Unconfigured slots default
  to `functionID=2`, `refreshType=0`.
- Adding a new connection means creating `variable_<id>` and
  `virtuino_value_<id>` tables (clone the DDL of an existing pair) and
  inserting all 500 default rows.

### Widget actions (`actionsListData` on button etc.)

JSON array of actions:

```json
{"uniqueID":<ms timestamp>, "actionTypeID":106,
 "actionTriggerParent":100, "actionTriggerID":0,
 "orderID":0, "actionDelayMillis":0,
 "panelSelectionType":1, "panelID":3}
```

Observed values:

| field | value | meaning |
|---|---|---|
| `actionTypeID` | 101 | set a variable's value (`connectionID`, `variableID`, `value`) |
| `actionTypeID` | 106 | select dashboard |
| `actionTriggerID` | 0 | button click |
| `actionTriggerID` | 2 | button long press |
| `panelSelectionType` | 0 | "select first dashboard" (ignores `panelID`) |
| `panelSelectionType` | 1 | select dashboard by ID (`panelID` = panel `_id`) |

### Misc

- Colors are Android ARGB stored as unsigned ints (white = `4294967295`).
- `button.disableDefaultOnAction/OffAction = 1` turns a switch into a pure
  action trigger (it stops writing `valueON/valueOFF` to its variable).
- Default screen grid: 384 × 832; panels scroll vertically.
- After edits: run `PRAGMA integrity_check` and update `sqlite_sequence`.
