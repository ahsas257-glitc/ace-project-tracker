"""Storage for the ACE Project Tracker.

Live deployments keep everything in one Google Sheet (one tab per table).
Set ACE_TRACKER_LOCAL=1 to explicitly use CSV files for local testing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

BASE = Path(__file__).parent
SEED = BASE / "seed_data"
LOCAL = BASE / "data"

# Column order for every tab. The first column is always the row id.
SCHEMA: dict[str, list[str]] = {
    "projects": ["id", "name", "short", "code", "client", "partners", "start", "end",
                 "week_start", "weeks", "plan_baseline", "summary", "order"],
    "actions": ["id", "project", "no", "title", "remarks"],
    "tasks": ["id", "project", "no", "seq", "sub", "owner", "due", "start", "end", "status",
              "progress", "actual_end", "note", "recurring", "milestone", "updated_at", "updated_by"],
    "indicators": ["id", "project", "seq", "group", "name", "unit", "target", "actual", "note",
                   "updated_at", "updated_by"],
    "log": ["id", "project", "ref", "label", "change", "by", "at"],
    "snaps": ["id", "project", "date", "actual", "planned"],
}


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return v.isoformat()[:10]
    return str(v)


class LocalStore:
    kind = "Local CSV files (testing only)"

    def __init__(self):
        LOCAL.mkdir(exist_ok=True)
        for tab in SCHEMA:
            p = LOCAL / f"{tab}.csv"
            if not p.exists():
                src = SEED / f"{tab}.csv"
                if src.exists():
                    shutil.copy(src, p)
                else:
                    pd.DataFrame(columns=SCHEMA[tab]).to_csv(p, index=False)

    def read(self, tab: str) -> pd.DataFrame:
        df = pd.read_csv(LOCAL / f"{tab}.csv", dtype=str, keep_default_na=False)
        for c in SCHEMA[tab]:
            if c not in df.columns:
                df[c] = ""
        return df

    def upsert(self, tab: str, rows: list[dict]):
        df = self.read(tab)
        for r in rows:
            r = {k: _clean(v) for k, v in r.items()}
            m = df["id"] == r["id"]
            if m.any():
                for k, v in r.items():
                    if k in df.columns:
                        df.loc[m, k] = v
            else:
                df = pd.concat([df, pd.DataFrame([{c: r.get(c, "") for c in df.columns}])], ignore_index=True)
        df.to_csv(LOCAL / f"{tab}.csv", index=False)

    def delete(self, tab: str, ids: list[str]):
        df = self.read(tab)
        df[~df["id"].isin(ids)].to_csv(LOCAL / f"{tab}.csv", index=False)

    def is_empty(self) -> bool:
        return self.read("projects").empty

    def load_seed(self):
        for tab in SCHEMA:
            shutil.copy(SEED / f"{tab}.csv", LOCAL / f"{tab}.csv")


class SheetStore:
    kind = "Google Sheet"

    def __init__(self, creds: dict, key: str):
        import gspread
        self._gspread = gspread
        gc = gspread.service_account_from_dict(creds)
        self.sh = gc.open_by_key(key)
        existing = {w.title: w for w in self.sh.worksheets()}
        self.ws = {}
        for tab, cols in SCHEMA.items():
            if tab in existing:
                ws = existing[tab]
                if not ws.row_values(1):
                    ws.update([cols], "A1")
            else:
                ws = self.sh.add_worksheet(title=tab, rows=200, cols=len(cols))
                ws.update([cols], "A1")
            self.ws[tab] = ws

    def _header(self, tab):
        return self.ws[tab].row_values(1)

    def read(self, tab: str) -> pd.DataFrame:
        vals = self.ws[tab].get_all_values()
        if not vals:
            return pd.DataFrame(columns=SCHEMA[tab])
        head = vals[0]
        body = [r + [""] * (len(head) - len(r)) for r in vals[1:] if any(c.strip() for c in r)]
        df = pd.DataFrame(body, columns=head, dtype=str)
        for c in SCHEMA[tab]:
            if c not in df.columns:
                df[c] = ""
        return df

    def upsert(self, tab: str, rows: list[dict]):
        ws = self.ws[tab]
        head = self._header(tab)
        missing = [c for c in SCHEMA[tab] if c not in head]
        if missing:
            head = head + missing
            ws.update([head], "A1")
        grid = ws.get_all_values()
        idc = head.index("id")
        ids = [row[idc] if len(row) > idc else "" for row in grid]
        updates, appends = [], []
        a1 = self._gspread.utils.rowcol_to_a1
        for r in rows:
            r = {k: _clean(v) for k, v in r.items()}
            if r["id"] in ids:
                i = ids.index(r["id"]) + 1
                current = list(grid[i - 1])
                current += [""] * (len(head) - len(current))
                line = [r.get(c, current[j]) if c in r else current[j] for j, c in enumerate(head)]
                updates.append({"range": f"{a1(i, 1)}:{a1(i, len(head))}", "values": [line]})
            else:
                appends.append([r.get(c, "") for c in head])
        if updates:
            ws.batch_update(updates, value_input_option="RAW")
        if appends:
            ws.append_rows(appends, value_input_option="RAW")

    def delete(self, tab: str, ids: list[str]):
        ws = self.ws[tab]
        col = ws.col_values(self._header(tab).index("id") + 1)
        for i in sorted([n + 1 for n, v in enumerate(col) if v in ids], reverse=True):
            ws.delete_rows(i)

    def is_empty(self) -> bool:
        return self.read("projects").empty

    def load_seed(self):
        for tab, cols in SCHEMA.items():
            df = pd.read_csv(SEED / f"{tab}.csv", dtype=str, keep_default_na=False)
            self.upsert(tab, df.reindex(columns=cols).fillna("").to_dict("records"))


@st.cache_resource(show_spinner=False)
def get_store():
    try:
        sec = st.secrets
        if sec.get("gcp_service_account") and sec.get("sheet_key"):
            return SheetStore(dict(sec["gcp_service_account"]), sec["sheet_key"])
    except FileNotFoundError:
        pass
    except Exception as exc:
        st.error(f"Could not open the Google Sheet ({type(exc).__name__}). Check the service account and sheet access.")
        st.stop()
    if os.environ.get("ACE_TRACKER_LOCAL") == "1":
        return LocalStore()
    st.error("Google Sheets storage requires sheet_key and gcp_service_account in Streamlit Secrets.")
    st.stop()


@st.cache_data(ttl=45, show_spinner=False)
def read_tab(tab: str) -> pd.DataFrame:
    return get_store().read(tab)


def write(tab: str, rows: list[dict]):
    if rows:
        get_store().upsert(tab, rows)
        read_tab.clear()


def remove(tab: str, ids: list[str]):
    if ids:
        get_store().delete(tab, ids)
        read_tab.clear()
