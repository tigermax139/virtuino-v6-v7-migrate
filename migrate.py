#!/usr/bin/env python3
"""
Virtuino 6 (.vrt6) -> Virtuino IoT (.vrt7) project migrator.

Both formats are SQLite databases, but with completely different schemas.
This tool reads Modbus switch widgets (digital outputs / coils) from a
Virtuino 6 project and rebuilds them inside a Virtuino IoT project.

IMPORTANT: Virtuino IoT encrypts server IP addresses inside the file, so a
migration cannot be done "from nothing". You must export a small TEMPLATE
project from the Virtuino IoT app first:

    1. In Virtuino IoT create a new project.
    2. Add one Modbus TCP connection (enter your real PLC IP).
    3. Add one Button widget and one Label widget (any settings).
    4. Export the project and use it as the --template argument.

The migrator clones the template's connection (keeping its encrypted IP),
button style and label style. After importing the result, open every
connection except the first one and re-type its IP address in the app.

Usage:
    python3 migrate.py old_project.vrt6 --template sample.vrt7 -o out.vrt7

Tested with: Virtuino 6 (user_version 102) -> Virtuino IoT (user_version 23),
Siemens LOGO!8 over Modbus TCP, coil-type switches.
Not supported (yet): charts, gauges, MQTT connections, analog widgets.
"""

import argparse
import json
import math
import shutil
import sqlite3
import sys

# ---------- layout constants for the generated dashboards ----------
SCREEN_W = 384          # default Virtuino IoT screen width
ROW_H = 52              # height of one switch row
BTN_W, BTN_H = 80, 44   # switch button size
LBL_COLOR = 4294967295  # white  (Android ARGB as unsigned int)
NAV_RESERVED = 0        # extra space on top (set >0 if you add nav buttons)

MODBUS_SERVER_TYPE = 300      # vrt7 connection.serverType for Modbus TCP
COIL_FUNCTION_ID = 0          # vrt7 variable functionID for coils (FC01/05)
VAR_POOL_SIZE = 500           # vrt7 pre-creates 500 variables per connection


def dictrows(cur, query, args=()):
    cols = [d[0] for d in cur.execute(query, args).description]
    cur2 = cur.execute(query, args)
    return [dict(zip(cols, r)) for r in cur2.fetchall()]


# =====================================================================
# 1. Read the old Virtuino 6 project
# =====================================================================
def read_vrt6(path):
    con = sqlite3.connect(path)
    cur = con.cursor()

    servers = dictrows(cur, "SELECT * FROM servers")
    panels = dictrows(cur, "SELECT * FROM panel")
    switches = dictrows(
        cur,
        "SELECT * FROM digital_output_component ORDER BY panelID, y, x")
    try:
        texts = dictrows(cur, "SELECT * FROM text")
    except sqlite3.OperationalError:
        texts = []
    con.close()

    # Match every switch with the nearest text label on the same panel.
    # In Virtuino 6 labels are separate widgets placed near the switch.
    for sw in switches:
        label = (sw.get("description") or sw.get("name") or "").strip()
        if not label:
            best, best_d = None, 1e12
            for t in texts:
                if t.get("panelID") != sw.get("panelID"):
                    continue
                d = math.hypot((t.get("x", 0) - sw.get("x", 0)),
                               (t.get("y", 0) - sw.get("y", 0)))
                if d < best_d:
                    best, best_d = t, d
            label = (best or {}).get("textValue", f"pin {sw.get('pin')}")
        sw["label"] = label

    return {"servers": servers, "panels": panels, "switches": switches}


# =====================================================================
# 2. Build the new Virtuino IoT project on top of the template
# =====================================================================
def migrate(old, template, out):
    shutil.copy(template, out)
    con = sqlite3.connect(out)
    cur = con.cursor()

    # ---- template pieces we clone -----------------------------------
    tconn = dictrows(
        cur, "SELECT * FROM connection WHERE serverType=? LIMIT 1",
        (MODBUS_SERVER_TYPE,))
    if not tconn:
        sys.exit("Template has no Modbus TCP connection - create one "
                 "in the Virtuino IoT app and export again.")
    tconn = tconn[0]

    tbtn = dictrows(cur, "SELECT * FROM button LIMIT 1")
    tlbl = dictrows(cur, "SELECT * FROM label LIMIT 1")
    if not tbtn or not tlbl:
        sys.exit("Template needs at least one Button and one Label widget.")
    tbtn, tlbl = tbtn[0], tlbl[0]

    tpanel = dictrows(cur, "SELECT * FROM panel WHERE panelType=100 LIMIT 1")[0]

    var_ddl = cur.execute(
        "SELECT sql FROM sqlite_master WHERE name=?",
        (f"variable_{tconn['_id']}",)).fetchone()[0]
    val_ddl = cur.execute(
        "SELECT sql FROM sqlite_master WHERE name=?",
        (f"virtuino_value_{tconn['_id']}",)).fetchone()[0]

    # wipe template widgets, keep its connection as connection #1 target
    cur.execute("DELETE FROM button")
    cur.execute("DELETE FROM label")

    # ---- old Modbus servers -> new connections ----------------------
    # In vrt6 the 'servers' table stores Modbus TCP servers with a
    # non-empty ipAddress (the emulator has type=3 and an empty IP).
    old_modbus = [s for s in old["servers"] if s.get("ipAddress")]

    conn_map = {}          # old serverID -> new connection id
    next_conn_id = tconn["_id"]
    ccols = list(tconn.keys())

    for i, srv in enumerate(old_modbus):
        extra = json.loads(tconn["extraData"])
        # carry over Modbus unit ids if the old project had them
        units = []
        try:
            old_extra = json.loads(srv.get("serverExtra") or "{}")
            unit_list = old_extra.get("unitList", [])
            if isinstance(unit_list, str):     # vrt6 double-encodes this JSON
                unit_list = json.loads(unit_list) if unit_list else []
            for j, u in enumerate(unit_list):
                units.append({
                    "nickname": u.get("name", f"unit {u.get('unitID', 1)}"),
                    "unitID": u.get("unitID", 1), "isEnabled": 1,
                    "forPLC": 0, "addressSizeID": 0,
                    "uniqueID": 1700000000000 + i * 100 + j})
        except (json.JSONDecodeError, TypeError):
            pass
        if not units:
            units = [{"nickname": "unit 1", "unitID": 1, "isEnabled": 1,
                      "forPLC": 0, "addressSizeID": 0,
                      "uniqueID": 1700000000000 + i * 100}]
        extra["unitListData"] = json.dumps(units)

        if i == 0:
            cur.execute(
                "UPDATE connection SET name=?, extraData=? WHERE _id=?",
                (srv.get("name", "Modbus"), json.dumps(extra), tconn["_id"]))
            new_id = tconn["_id"]
        else:
            next_conn_id += 1
            new_id = next_conn_id
            row = dict(tconn)
            row["_id"] = new_id
            row["name"] = srv.get("name", f"Modbus {i+1}")
            row["extraData"] = json.dumps(extra)
            cur.execute(
                f"INSERT INTO connection ({','.join(ccols)}) "
                f"VALUES ({','.join('?' * len(ccols))})",
                [row[c] for c in ccols])
            # per-connection variable tables
            cur.execute(var_ddl.replace(
                f"variable_{tconn['_id']}", f"variable_{new_id}"))
            cur.execute(val_ddl.replace(
                f"virtuino_value_{tconn['_id']}", f"virtuino_value_{new_id}"))
            default_extra = json.dumps({
                "multiInputID": 0, "unitID": 0, "address": 0,
                "valueFormatID": 100, "functionID": 2, "multiplier": 1.0,
                "refreshType": 0, "refresh": 5000,
                "multipleReadingType": 0, "multipleReadingCount": 0})
            for v in range(VAR_POOL_SIZE):
                cur.execute(
                    f"INSERT INTO variable_{new_id} VALUES "
                    f"(?,?,0,0,?,?,0,'','',?)",
                    (v, f"V{v}", new_id, MODBUS_SERVER_TYPE, default_extra))
                cur.execute(
                    f"INSERT INTO virtuino_value_{new_id} VALUES (?,?,'',0)",
                    (v, new_id))
        conn_map[srv["ID"]] = (new_id, units[0]["uniqueID"])

    # ---- old panels -> new panels -----------------------------------
    pcols = list(tpanel.keys())
    panel_map = {}
    next_panel_id = max(r[0] for r in cur.execute("SELECT _id FROM panel"))
    for i, p in enumerate(old["panels"]):
        if i == 0:
            cur.execute("UPDATE panel SET name=?, orderID=0 WHERE _id=?",
                        (p.get("name", "Home"), tpanel["_id"]))
            panel_map[p["ID"]] = tpanel["_id"]
        else:
            next_panel_id += 1
            row = dict(tpanel)
            row.update(_id=next_panel_id, name=p.get("name", f"Panel {i+1}"),
                       isLaunchPanel=0, orderID=i)
            cur.execute(
                f"INSERT INTO panel ({','.join(pcols)}) "
                f"VALUES ({','.join('?' * len(pcols))})",
                [row[c] for c in pcols])
            panel_map[p["ID"]] = next_panel_id

    # ---- switches -> variable + label + button ----------------------
    bcols, lcols = list(tbtn.keys()), list(tlbl.keys())
    btn_id = lbl_id = 0
    y_cursor, order = {}, {}
    var_cursor = {}   # per connection: next free variable slot

    for sw in old["switches"]:
        new_conn, unit_uid = conn_map[sw["serverID"]]
        vid = var_cursor.get(new_conn, 0)
        var_cursor[new_conn] = vid + 1
        vextra = json.dumps({
            "multiInputID": 0, "unitID": unit_uid,
            "address": int(sw["pin"]), "valueFormatID": 100,
            "functionID": COIL_FUNCTION_ID, "multiplier": 1.0,
            "refreshType": 1, "refresh": 5000,
            "multipleReadingType": 0, "multipleReadingCount": 1})
        cur.execute(
            f"UPDATE variable_{new_conn} SET name=?, type=1, extraData=? "
            f"WHERE _id=?", (sw["label"][:30], vextra, vid))

        panel = panel_map[sw["panelID"]]
        y = y_cursor.get(panel, 10 + NAV_RESERVED)
        o = order.get(panel, 0)

        lbl_id += 1
        lrow = dict(tlbl)
        lrow.update(_id=lbl_id, name=sw["label"][:20], panelID=panel,
                    orderID=o, left=10.0, top=float(y + 6),
                    width=float(SCREEN_W - BTN_W - 30), height=34.0,
                    right=float(SCREEN_W - BTN_W - 20), bottom=float(y + 40),
                    Label=sw["label"], color=LBL_COLOR,
                    textHeight=0.55, hasFrame=0)
        cur.execute(f"INSERT INTO label ({','.join(lcols)}) "
                    f"VALUES ({','.join('?' * len(lcols))})",
                    [lrow[c] for c in lcols])

        btn_id += 1
        brow = dict(tbtn)
        brow.update(_id=btn_id, name=sw["label"][:20], panelID=panel,
                    orderID=o + 1, left=float(SCREEN_W - BTN_W - 16),
                    top=float(y), width=float(BTN_W), height=float(BTN_H),
                    right=float(SCREEN_W - 16), bottom=float(y + BTN_H),
                    connectionID=new_conn, variableID=vid,
                    actionsListData="[]")
        cur.execute(f"INSERT INTO button ({','.join(bcols)}) "
                    f"VALUES ({','.join('?' * len(bcols))})",
                    [brow[c] for c in bcols])

        y_cursor[panel] = y + ROW_H
        order[panel] = o + 2

    # ---- bookkeeping -------------------------------------------------
    for name, seq in [("connection", next_conn_id),
                      ("panel", next_panel_id),
                      ("button", btn_id), ("label", lbl_id)]:
        cur.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?",
                    (seq, name))
    con.commit()
    ok = cur.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()

    print(f"Done: {out}")
    print(f"  connections: {len(conn_map)}  panels: {len(panel_map)}  "
          f"switches: {btn_id}  integrity: {ok}")
    print("\nAFTER IMPORT (in the Virtuino IoT app):")
    print("  1. Open every connection and re-type its IP address")
    print("     (IPs are encrypted; the file carries the template's IP).")
    print("  2. Test ONE switch against the real PLC before trusting all.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("vrt6", help="old Virtuino 6 project (.vrt6)")
    ap.add_argument("--template", required=True,
                    help="template .vrt7 exported from Virtuino IoT")
    ap.add_argument("-o", "--out", default="migrated.vrt7")
    args = ap.parse_args()
    migrate(read_vrt6(args.vrt6), args.template, args.out)
