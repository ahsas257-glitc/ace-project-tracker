"""ACE Project Tracker — Streamlit app.

Project officers update their Detailed Implementation Plan (DIP) sub-actions;
executives follow progress against plan.  Run locally with:
    streamlit run app.py
"""
from __future__ import annotations

import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from storage import get_store, read_tab, remove, write

st.set_page_config(page_title="ACE Project Tracker", page_icon="📊", layout="wide")

STATUSES = ["Not started", "In progress", "Completed", "Delayed", "On hold", "Ongoing"]
COLORS = {"Not started": "#8A94A3", "In progress": "#2346A0", "Completed": "#2C7A4B",
          "Delayed": "#BE3A2E", "On hold": "#B9810F", "Ongoing": "#1A7782", "Overdue": "#BE3A2E"}
TODAY = date.today()

st.markdown("""
<style>
[data-testid="stMetricValue"]{font-family:"Source Serif 4",Georgia,serif;font-weight:600}
.ace-chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:600;margin-right:4px}
.ace-ms{border:1px solid rgba(128,128,128,.3);border-radius:8px;padding:8px 10px;margin-bottom:6px}
.ace-ms .d{font-size:12px;opacity:.7;font-family:monospace}
.ace-ms .n{font-weight:600}
.block-container{padding-top:2.2rem}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
def d(s) -> date | None:
    try:
        if s is None or pd.isna(s):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s).strip()
    if not s:
        return None
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], f).date()
        except ValueError:
            pass
    try:
        return pd.to_datetime(s).date()
    except Exception:
        return None


def num(x, default=0.0) -> float:
    try:
        v = float(str(x).replace(",", "").replace("%", ""))
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def truthy(x) -> bool:
    return str(x).strip().upper() in ("TRUE", "1", "YES", "Y")


def fmt(x: date | None) -> str:
    return f"{x.day} {x:%b %Y}" if x else "—"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def chip(text, color):
    return f'<span class="ace-chip" style="background:{color}22;color:{color}">{text}</span>'


# ---------------------------------------------------------------- data prep
def prep_tasks(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    if t.empty:
        for c in ["no", "seq", "progress", "start_d", "end_d", "actual_d", "rec", "flag", "days"]:
            t[c] = []
        return t
    t["no"] = t["no"].map(lambda v: int(num(v)))
    t["seq"] = t["seq"].map(lambda v: int(num(v)))
    t["status"] = t["status"].replace("", "Not started")
    t["progress"] = t.apply(lambda r: 100.0 if r.status == "Completed" else max(0.0, min(100.0, num(r.progress))), axis=1)
    t["start_d"] = t["start"].map(d)
    t["end_d"] = t["end"].map(d)
    t["start_d"] = t["start_d"].fillna(TODAY)
    t["end_d"] = [e if e else s for e, s in zip(t["end_d"], t["start_d"])]
    t["actual_d"] = t["actual_end"].map(d)
    t["rec"] = t["recurring"].map(truthy)
    flags, days = [], []
    for r in t.itertuples():
        f, n = "", 0
        if not r.rec and r.status != "Completed":
            if r.end_d < TODAY:
                f, n = "Overdue", (TODAY - r.end_d).days
            elif r.start_d <= TODAY and r.status == "Not started" and (TODAY - r.start_d).days >= 3:
                f, n = "Late start", (TODAY - r.start_d).days
        flags.append(f)
        days.append(n)
    t["flag"], t["days"] = flags, days
    t["ref"] = t["no"].astype(str) + "." + t["seq"].astype(str)
    return t.sort_values(["no", "seq"]).reset_index(drop=True)


def planned_frac(r, on: date) -> float:
    span = (r.end_d - r.start_d).days + 1
    return max(0.0, min(1.0, ((on - r.start_d).days + 1) / span)) if span > 0 else 0.0


def overall(t: pd.DataFrame, on: date) -> tuple[float, float]:
    m = t[~t["rec"]] if not t.empty else t
    if m.empty:
        return 0.0, 0.0
    actual = m["progress"].mean()
    planned = sum(planned_frac(r, on) for r in m.itertuples()) / len(m) * 100
    return actual, planned


def log(pid, ref, label, change):
    if change:
        write("log", [{"id": uuid.uuid4().hex[:12], "project": pid, "ref": ref, "label": label[:200],
                       "change": change[:500], "by": st.session_state.get("who", ""), "at": now_iso()}])


def snapshot(pid):
    t = prep_tasks(read_tab("tasks").query("project == @pid"))
    a, p = overall(t, TODAY)
    write("snaps", [{"id": f"{pid}_{TODAY.isoformat()}", "project": pid, "date": TODAY.isoformat(),
                     "actual": round(a, 1), "planned": round(p, 1)}])


# ---------------------------------------------------------------- sign-in
def sign_in():
    sec = {}
    try:
        sec = dict(st.secrets)
    except Exception:
        pass
    vpw, epw = sec.get("viewer_password"), sec.get("editor_password")
    st.session_state["has_login"] = bool(vpw or epw)
    if not vpw and not epw:  # local testing without secrets
        st.session_state.setdefault("role", "editor")
        st.session_state.setdefault("who", "Local tester")
        return
    if st.session_state.get("role"):
        return
    st.title("ACE Project Tracker")
    st.caption("Afghanistan Center for Excellence")
    with st.form("login"):
        pw = st.text_input("Password", type="password")
        who = st.text_input("Your name (project officers only — shown in the change log)")
        ok = st.form_submit_button("Sign in", type="primary")
    if ok:
        if epw and pw == epw:
            if not who.strip():
                st.error("Project officers: enter your name so updates are credited to you.")
                st.stop()
            st.session_state.role, st.session_state.who = "editor", who.strip()
            st.rerun()
        elif vpw and pw == vpw:
            st.session_state.role, st.session_state.who = "viewer", ""
            st.rerun()
        else:
            st.error("That password isn't right.")
    st.stop()


sign_in()
EDITOR = st.session_state.get("role") == "editor"
store = get_store()

# ---------------------------------------------------------------- empty sheet
if store.is_empty():
    st.title("ACE Project Tracker")
    st.info("The tracker has no projects yet.")
    if EDITOR and st.button("Load the UNDP A2F plan (DIP 7 Oct 2026)", type="primary"):
        store.load_seed()
        read_tab.clear()
        st.rerun()
    st.stop()

# ---------------------------------------------------------------- sidebar
projects = read_tab("projects").copy()
projects["order_n"] = projects["order"].map(num)
projects = projects.sort_values("order_n")
labels = {r.id: (r.short or r.name) for r in projects.itertuples()}
if st.session_state.get("_next_pid") in labels:
    st.session_state["pid"] = st.session_state.pop("_next_pid")
with st.sidebar:
    st.markdown("### ACE Project Tracker")
    st.caption("Afghanistan Center for Excellence")
    pid = st.selectbox("Project", list(labels), format_func=labels.get, key="pid")
    page = st.radio("View", ["Overview", "Implementation plan", "Update progress", "Indicators",
                             "Change log", "Projects"], key="page")
    st.divider()
    st.caption(("Signed in as **" + st.session_state.get("who", "") + "** (can edit)") if EDITOR else "Signed in as viewer")
    st.caption(f"Data: {store.kind}")
    if st.button("Refresh data"):
        read_tab.clear()
        st.rerun()
    if st.session_state.get("has_login"):
        if st.button("Sign out"):
            for k in ("role", "who"):
                st.session_state.pop(k, None)
            st.rerun()

P = projects.set_index("id").loc[pid]
T = prep_tasks(read_tab("tasks").query("project == @pid"))
A = read_tab("actions").query("project == @pid").copy()
A["no"] = A["no"].map(lambda v: int(num(v)))
ATITLE = dict(zip(A["no"], A["title"]))
AREM = dict(zip(A["no"], A["remarks"]))
I = read_tab("indicators").query("project == @pid").copy()
L = read_tab("log").query("project == @pid").sort_values("at", ascending=False)
SN = read_tab("snaps").query("project == @pid").copy()
p_start, p_end = d(P["start"]) or TODAY, d(P["end"]) or TODAY
w_start = d(P["week_start"]) or p_start
weeks = int(num(P["weeks"]))


def action_title(no):
    return ATITLE.get(no, f"Action {no}")


# ================================================================ OVERVIEW
def page_overview():
    st.caption(P["client"])
    st.title(P["name"])
    if P["summary"]:
        st.write(P["summary"])
    wk = max(1, (TODAY - w_start).days // 7 + 1)
    elapsed = max(0.0, min(1.0, ((TODAY - p_start).days) / max(1, (p_end - p_start).days + 1)))
    meta = [f"Ref **{P['code']}**" if P["code"] else "", f"Implemented by **{P['partners']}**" if P["partners"] else "",
            f"Baseline **{P['plan_baseline']}**" if P["plan_baseline"] else "",
            f"{fmt(p_start)} – {fmt(p_end)}"]
    st.markdown(" · ".join(m for m in meta if m))
    wk_txt = f"Week {min(wk, weeks)} of {weeks}" if weeks else f"Day {(TODAY - p_start).days + 1}"
    st.progress(elapsed, text=f"{wk_txt} · today {fmt(TODAY)} · {elapsed:.0%} of time elapsed")

    a, p = overall(T, TODAY)
    M = T[~T["rec"]]
    c = st.columns(5)
    c[0].metric("Actual progress", f"{a:.0f}%", f"{a - p:+.0f} pts vs plan", delta_color="normal")
    c[1].metric("Planned by today", f"{p:.0f}%", help="Average of each sub-action's elapsed share of its planned dates")
    c[2].metric("Sub-actions completed", f"{(M.status == 'Completed').sum()} / {len(M)}",
                f"{(M.status == 'In progress').sum()} in progress", delta_color="off")
    c[3].metric("Overdue", int((T.flag == "Overdue").sum()), help="Past planned end date and not completed")
    c[4].metric("Late start", int((T.flag == "Late start").sum()), help="Planned start passed, still not started")

    ms = T[T["milestone"].str.strip() != ""].sort_values("end_d")
    if not ms.empty:
        st.subheader("Milestones and deliverables")
        cols = st.columns(min(len(ms), 7))
        for i, r in enumerate(ms.itertuples()):
            if r.status == "Completed":
                lab, col, when = "Done", COLORS["Completed"], f"Done {fmt(r.actual_d)}" if r.actual_d else f"Due {fmt(r.end_d)}"
            elif r.flag:
                lab, col, when = r.flag, COLORS["Delayed"] if r.flag == "Overdue" else COLORS["On hold"], f"Due {fmt(r.end_d)}"
            else:
                lab, col, when = r.status, COLORS.get(r.status, "#888"), f"Due {fmt(r.end_d)}"
            cols[i % len(cols)].markdown(f'<div class="ace-ms"><div class="d">{when}</div><div class="n">{r.milestone}</div>{chip(lab, col)}</div>',
                                         unsafe_allow_html=True)

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Planned vs actual progress")
        st.plotly_chart(curve_fig(), width="stretch", config={"displayModeBar": False})
    with right:
        st.subheader("Progress by action")
        st.plotly_chart(action_fig(), width="stretch", config={"displayModeBar": False})

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Needs attention")
        att = T[(T.flag != "") | T.status.isin(["Delayed", "On hold"])].sort_values("days", ascending=False)
        if att.empty:
            st.success("Nothing overdue.")
        for r in att.head(12).itertuples():
            tag = f"{r.flag} · {r.days} d" if r.flag else r.status
            st.markdown(f"{chip(tag, COLORS['Delayed'] if r.flag == 'Overdue' else COLORS['On hold'])} **{r.ref}** {r.sub}  \n"
                        f"<small>{r.owner or 'No owner'} · ends {fmt(r.end_d)} · {r.progress:.0f}%</small>", unsafe_allow_html=True)
        if len(att) > 12:
            st.caption(f"+ {len(att) - 12} more — see Update progress")
    with c2:
        st.subheader("Coming up in 14 days")
        h = TODAY + timedelta(days=14)
        up = T[(~T.rec) & (T.status != "Completed") & (((T.start_d > TODAY) & (T.start_d <= h)) | ((T.end_d >= TODAY) & (T.end_d <= h)))].sort_values("end_d")
        if up.empty:
            st.caption("Nothing starts or falls due in the next two weeks.")
        for r in up.head(12).itertuples():
            when = f"Starts {fmt(r.start_d)}" if r.start_d > TODAY else f"Due {fmt(r.end_d)}"
            st.markdown(f"{chip(when, '#5A6577')} **{r.ref}** {r.sub}  \n<small>{r.owner or 'No owner'} · {r.progress:.0f}%</small>",
                        unsafe_allow_html=True)
    with c3:
        st.subheader("Key indicators")
        if I.empty:
            st.caption("No indicators set.")
        for r in I.assign(s=I["seq"].map(num)).sort_values("s").itertuples():
            tg, ac = num(r.target), num(r.actual)
            st.progress(min(1.0, ac / tg) if tg else 0.0,
                        text=f"{r.name}: **{ac:,.0f}**" + (f" / {tg:,.0f}" if tg else " (no target)"))

    st.subheader("Recent updates")
    if L.empty:
        st.caption("No updates yet. Changes saved under Update progress appear here.")
    else:
        show = L.head(10)[["at", "by", "label", "change"]].copy()
        show["at"] = pd.to_datetime(show["at"], errors="coerce", utc=True).dt.strftime("%d %b %H:%M").str.lstrip("0")
        st.dataframe(show.rename(columns={"at": "When (UTC)", "by": "Who", "label": "Item", "change": "Change"}),
                     hide_index=True, width="stretch")


def curve_fig():
    M = T[~T.rec]
    fig = go.Figure()
    if M.empty:
        return fig
    s0 = min(p_start, M.start_d.min())
    e0 = max(p_end, M.end_d.max())
    days = pd.date_range(s0, e0, freq="3D").date.tolist() + [e0]
    fig.add_scatter(x=days, y=[overall(T, x)[1] for x in days], name="Planned", mode="lines",
                    line=dict(color="#8A94A3", dash="dash", width=2),
                    hovertemplate="%{x|%d %b %Y}<br>Planned %{y:.0f}%<extra></extra>")
    snaps = SN.assign(dd=SN["date"].map(d), v=SN["actual"].map(num)).dropna(subset=["dd"])
    snaps = snaps[snaps.dd < TODAY].sort_values("dd")
    xs = [s0] + snaps.dd.tolist() + [TODAY]
    ys = [0] + snaps.v.tolist() + [overall(T, TODAY)[0]]
    fig.add_scatter(x=xs, y=ys, name="Actual", mode="lines+markers", fill="tozeroy",
                    line=dict(color="#2346A0", width=3), fillcolor="rgba(35,70,160,.12)",
                    marker=dict(size=[0] * (len(xs) - 1) + [10]),
                    hovertemplate="%{x|%d %b %Y}<br>Actual %{y:.0f}%<extra></extra>")
    fig.add_vline(x=datetime.combine(TODAY, datetime.min.time()).timestamp() * 1000, line=dict(color="#BE3A2E", dash="dot", width=1),
                  annotation_text="Today", annotation_font_color="#BE3A2E")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(range=[0, 102], ticksuffix="%"),
                      legend=dict(orientation="h", y=1.08, x=0), hovermode="x unified")
    return fig


def action_fig():
    rows = []
    for no, g in T.groupby("no"):
        m = g[~g.rec]
        if m.empty:
            rows.append((no, None, None, False))
        else:
            rows.append((no, m.progress.mean(), sum(planned_frac(r, TODAY) for r in m.itertuples()) / len(m) * 100,
                         (g.flag == "Overdue").any()))
    rows.sort(key=lambda r: -r[0])
    y = [f"{no:02d} " + (action_title(no) if len(action_title(no)) <= 42 else action_title(no)[:41] + "…") for no, *_ in rows]
    act = [100 if a is None else a for _, a, _, _ in rows]
    col = [COLORS["Ongoing"] if a is None else COLORS["Completed"] if a >= 99.5 else COLORS["Overdue"] if od
           else COLORS["On hold"] if pl - a > 10 else COLORS["In progress"] for _, a, pl, od in rows]
    txt = ["Ongoing" if a is None else f"{a:.0f}%" for _, a, _, _ in rows]
    fig = go.Figure()
    fig.add_bar(y=y, x=act, orientation="h", marker=dict(color=col, opacity=[0.45 if a is None else 1 for _, a, _, _ in rows]),
                text=txt, textposition="outside", name="Actual", cliponaxis=False,
                hovertemplate="%{y}<br>Actual %{text}<extra></extra>")
    fig.add_scatter(y=y, x=[pl for *_, pl, _ in rows], mode="markers", name="Planned by today",
                    marker=dict(symbol="line-ns", size=14, line=dict(width=3, color="#B9810F")),
                    hovertemplate="Planned %{x:.0f}%<extra></extra>")
    fig.update_layout(height=max(330, 24 * len(rows) + 60), margin=dict(l=10, r=40, t=10, b=10),
                      xaxis=dict(range=[0, 112], ticksuffix="%"), legend=dict(orientation="h", y=1.04, x=0),
                      bargap=0.35, yaxis=dict(tickfont=dict(size=11)))
    return fig


# ================================================================ PLAN
def page_plan():
    st.title("Detailed implementation plan")
    st.caption("Bars show planned dates, coloured by status; red bars are overdue. Hover for detail.")
    c1, c2 = st.columns([1, 3])
    level = c1.radio("Show", ["Actions", "Sub-actions"], horizontal=True)
    chosen = c2.multiselect("Actions", sorted(T.no.unique()), format_func=lambda n: f"{n:02d} {action_title(n)}")
    G = T[T.no.isin(chosen)] if chosen else T
    if level == "Actions":
        rows = []
        for no, g in G.groupby("no"):
            m = g[~g.rec]
            a = m.progress.mean() if not m.empty else None
            st_ = "Ongoing" if a is None else "Completed" if a >= 99.5 else "Overdue" if (g.flag == "Overdue").any() else "In progress" if a > 0 else "Not started"
            rows.append(dict(label=f"{no:02d} " + (action_title(no) if len(action_title(no)) <= 56 else action_title(no)[:55] + "…"), start=g.start_d.min(), end=g.end_d.max() + timedelta(days=1),
                             status=st_, info=f"{'Ongoing' if a is None else f'{a:.0f}%'} · {len(g)} sub-actions"))
    else:
        rows = [dict(label=f"{r.ref} " + (r.sub if len(r.sub) <= 56 else r.sub[:55] + "…"), start=r.start_d, end=r.end_d + timedelta(days=1),
                     status="Overdue" if r.flag == "Overdue" else r.status,
                     info=f"{r.status} {r.progress:.0f}% · {r.owner}") for r in G.itertuples()]
    df = pd.DataFrame(rows)
    fig = go.Figure()
    for stt in df.status.unique():
        s = df[df.status == stt]
        fig.add_bar(base=pd.to_datetime(s.start), x=(pd.to_datetime(s.end) - pd.to_datetime(s.start)).dt.days * 86400000,
                    y=s.label, orientation="h", name=stt, marker_color=COLORS.get(stt, "#888"),
                    customdata=s[["info"]].assign(a=pd.to_datetime(s.start).dt.strftime("%d %b"),
                                                  b=(pd.to_datetime(s.end) - pd.Timedelta(days=1)).dt.strftime("%d %b %Y")).values,
                    hovertemplate="%{y}<br>%{customdata[1]} – %{customdata[2]}<br>%{customdata[0]}<extra></extra>")
    fig.add_vline(x=datetime.combine(TODAY, datetime.min.time()).timestamp() * 1000, line=dict(color="#BE3A2E", width=2),
                  annotation_text="Today", annotation_font_color="#BE3A2E")
    step = 2 if (weeks or 20) > 12 else 1
    ticks = [w_start + timedelta(days=7 * k) for k in range(0, (weeks or 20) + 1, step)]
    fig.update_layout(barmode="overlay", height=max(380, 26 * len(df) + 80), margin=dict(l=10, r=10, t=30, b=10),
                      yaxis=dict(autorange="reversed", tickfont=dict(size=11), categoryorder="array", categoryarray=df.label.tolist()),
                      xaxis=dict(type="date", tickvals=ticks, ticktext=[f"W{k * step + 1}<br>{t.day} {t:%b}" for k, t in enumerate(ticks)], tickangle=0, side="top"),
                      legend=dict(orientation="h", y=-0.02))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    out = T[["ref", "sub", "owner", "start_d", "end_d", "due", "status", "progress", "flag", "actual_d", "note"]].copy()
    out.insert(1, "action", T.no.map(action_title))
    out.columns = ["No", "Action", "Sub-action", "Responsible", "Planned start", "Planned end", "DIP timing", "Status",
                   "Progress %", "Schedule flag", "Actual completion", "Officer note"]
    buf = io.BytesIO()
    out.to_excel(buf, index=False, sheet_name="DIP progress")
    st.download_button("Download plan with progress (Excel)", buf.getvalue(),
                       file_name=f"{(P['short'] or 'project').replace(' ', '_')}_DIP_{TODAY}.xlsx")


# ================================================================ UPDATE
def page_update():
    st.title("Update progress")
    if not EDITOR:
        st.warning("You are signed in as a viewer. Sign in with the project officer password to make changes.")
    st.caption("Change Status, % complete, actual completion date or note in the table, then press **Save changes**. "
               "Setting Completed fills 100% and today's date; any % above 0 moves a sub-action to In progress.")
    owners = sorted({o.strip() for v in T.owner for o in re.split(r"/|,", re.sub(r"\(.*?\)", "", v)) if o.strip()})
    c = st.columns([2, 1.3, 1, 1, 1])
    q = c[0].text_input("Search", placeholder="Sub-action, owner or note")
    who = c[1].selectbox("Responsible", ["All"] + owners)
    sts = c[2].selectbox("Status", ["All"] + STATUSES + ["Overdue", "Late start"])
    when = c[3].selectbox("Timing", ["Any", "Active now", "Due in 14 days", "Past end date"])
    details = c[4].toggle("Edit details", help="Also edit description, owner, dates and milestone", disabled=not EDITOR)
    V = T.copy()
    if q:
        ql = q.lower()
        V = V[(V["sub"] + " " + V["owner"] + " " + V["note"] + " " + V["no"].map(action_title)).str.lower().str.contains(ql, regex=False)]
    if who != "All":
        V = V[V.owner.str.contains(who, regex=False)]
    if sts in ("Overdue", "Late start"):
        V = V[V.flag == sts]
    elif sts != "All":
        V = V[V.status == sts]
    if when == "Active now":
        V = V[(V.start_d <= TODAY) & (V.end_d >= TODAY)]
    elif when == "Due in 14 days":
        V = V[(V.status != "Completed") & (V.end_d >= TODAY) & (V.end_d <= TODAY + timedelta(days=14))]
    elif when == "Past end date":
        V = V[V.end_d < TODAY]

    E = pd.DataFrame({
        "id": V["id"], "Ref": V["ref"], "Sub-action": V["sub"],
        "Status": V["status"], "Progress %": V["progress"].astype(int), "Actual completion": V["actual_d"],
        "Note": V["note"], "Flag": [f"{f} {n} d" if f else "" for f, n in zip(V.flag, V.days)],
        "Responsible": V["owner"], "Start": V["start_d"], "End": V["end_d"],
        "Milestone": V["milestone"], "Recurring": V["rec"],
        "Action": V["no"].map(lambda n: f"{n:02d} {action_title(n)}")})
    editable = {"Status", "Progress %", "Actual completion", "Note"} | ({"Sub-action", "Responsible", "Start", "End", "Milestone", "Recurring"} if details else set())
    cfg = {
        "id": None,
        "Ref": st.column_config.TextColumn(width="small"),
        "Action": st.column_config.TextColumn(width="medium"),
        "Sub-action": st.column_config.TextColumn(width="large"),
        "Flag": st.column_config.TextColumn("Schedule", help="Overdue = past planned end; Late start = start date passed, not started"),
        "Start": st.column_config.DateColumn(format="D MMM YYYY"),
        "End": st.column_config.DateColumn(format="D MMM YYYY"),
        "Status": st.column_config.SelectboxColumn(options=STATUSES, required=True),
        "Progress %": st.column_config.NumberColumn(min_value=0, max_value=100, step=5),
        "Actual completion": st.column_config.DateColumn(format="D MMM YYYY"),
        "Note": st.column_config.TextColumn(width="medium"),
        "Milestone": st.column_config.TextColumn(help="Optional label, e.g. 'D2 ToT packs'"),
        "Recurring": st.column_config.CheckboxColumn(help="Recurring work is not counted in % progress"),
    }
    if not details:
        cfg["Milestone"] = None
        cfg["Recurring"] = None
    st.caption(f"{len(E)} of {len(T)} sub-actions shown")
    ed = st.data_editor(E, column_config=cfg, hide_index=True, width="stretch",
                        disabled=[c for c in E.columns if c not in editable] if EDITOR else True,
                        height=min(720, 38 + 35 * max(len(E), 3)), key=f"ed_{pid}_{details}_{q}_{who}_{sts}_{when}")
    if EDITOR and st.button("Save changes", type="primary"):
        save_edits(E, ed)

    if EDITOR:
        st.divider()
        a1, a2, a3 = st.columns(3)
        with a1, st.expander("Add a sub-action"):
            with st.form("add_task", clear_on_submit=True):
                nos = sorted(set(T.no) | set(A.no))
                no = st.selectbox("Action", nos + ["New action"], format_func=lambda n: n if n == "New action" else f"{n:02d} {action_title(n)}")
                new_title = st.text_input("New action title (only if 'New action')")
                sub = st.text_area("Sub-action")
                owner = st.text_input("Responsible")
                s = st.date_input("Planned start", TODAY)
                e = st.date_input("Planned end", TODAY + timedelta(days=7))
                if st.form_submit_button("Add"):
                    if not sub.strip():
                        st.error("Describe the sub-action.")
                    elif e < s:
                        st.error("The end date is before the start date.")
                    else:
                        if no == "New action":
                            no = (max(nos) if nos else 0) + 1
                            write("actions", [{"id": f"{pid}-{no:02d}", "project": pid, "no": no, "title": new_title.strip() or f"Action {no}", "remarks": ""}])
                        seq = int(T[T.no == no].seq.max()) + 1 if (T.no == no).any() else 1
                        tid = f"{pid}-{no:02d}-{seq}-{uuid.uuid4().hex[:4]}"
                        write("tasks", [{"id": tid, "project": pid, "no": no, "seq": seq, "sub": sub.strip(), "owner": owner.strip(),
                                         "due": "", "start": s.isoformat(), "end": e.isoformat(), "status": "Not started", "progress": 0,
                                         "actual_end": "", "note": "", "recurring": "FALSE", "milestone": "",
                                         "updated_at": now_iso(), "updated_by": st.session_state.get("who", "")}])
                        log(pid, f"{no}.{seq}", sub, "Added sub-action")
                        st.success("Added.")
                        st.rerun()
        with a2, st.expander("Edit an action's title or remarks"):
            nos = sorted(set(T.no) | set(A.no))
            no = st.selectbox("Action ", nos, format_func=lambda n: f"{n:02d} {action_title(n)}", key="act_pick")
            with st.form(f"act_{no}"):
                t_ = st.text_area("Title", action_title(no))
                r_ = st.text_area("Remarks", AREM.get(no, ""), height=150)
                if st.form_submit_button("Save action"):
                    write("actions", [{"id": f"{pid}-{no:02d}", "project": pid, "no": no, "title": t_.strip(), "remarks": r_.strip()}])
                    log(pid, str(no), t_, "Action title/remarks updated")
                    st.success("Saved.")
                    st.rerun()
        with a3, st.expander("Delete a sub-action"):
            opts = T["id"].tolist()
            lab = dict(zip(T["id"], T["ref"] + " " + T["sub"].str[:70]))
            tid = st.selectbox("Sub-action", opts, format_func=lab.get, key="del_pick")
            sure = st.checkbox("Yes, delete it permanently")
            if st.button("Delete", disabled=not sure):
                remove("tasks", [tid])
                log(pid, lab[tid].split(" ")[0], lab[tid], "Deleted sub-action")
                st.rerun()
        if AREM:
            with st.expander("Action remarks from the DIP"):
                for n in sorted(AREM):
                    if AREM[n]:
                        st.markdown(f"**{n:02d} {action_title(n)}** — {AREM[n]}")


def save_edits(before: pd.DataFrame, after: pd.DataFrame):
    b = before.set_index("id")
    rows, logs = [], []
    for _, r in after.iterrows():
        o = b.loc[r["id"]]
        status, prog_ = r["Status"], int(num(r["Progress %"]))
        actual = d(r["Actual completion"])
        # keep status and % consistent
        if status != o["Status"]:
            if status == "Completed":
                prog_ = 100
            elif status == "Not started" and prog_ == int(o["Progress %"]):
                prog_ = 0
        elif prog_ != int(o["Progress %"]):
            if prog_ >= 100:
                status = "Completed"
            elif prog_ > 0 and status in ("Not started", "Completed"):
                status = "In progress"
        if status == "Completed" and o["Status"] != "Completed" and not actual:
            actual = TODAY
        changes, desc = {}, []
        if status != o["Status"]:
            changes["status"] = status
            desc.append(f"Status {o['Status']} → {status}")
        if prog_ != int(o["Progress %"]) and status != "Ongoing":
            changes["progress"] = prog_
            desc.append(f"{int(o['Progress %'])}% → {prog_}%")
        if actual != d(o["Actual completion"]):
            changes["actual_end"] = actual.isoformat() if actual else ""
            if actual:
                desc.append(f"Completed on {fmt(actual)}")
        note = "" if pd.isna(r["Note"]) else str(r["Note"])
        if note != str(o["Note"]):
            changes["note"] = note
            desc.append("Note: " + note[:140])
        for col, key in [("Sub-action", "sub"), ("Responsible", "owner"), ("Milestone", "milestone")]:
            v = "" if pd.isna(r[col]) else str(r[col])
            if v != str(o[col]):
                changes[key] = v
                desc.append(f"{col} changed")
        for col, key in [("Start", "start"), ("End", "end")]:
            if d(r[col]) and d(r[col]) != d(o[col]):
                changes[key] = d(r[col]).isoformat()
                desc.append(f"{col} {fmt(d(o[col]))} → {fmt(d(r[col]))}")
        if bool(r["Recurring"]) != bool(o["Recurring"]):
            changes["recurring"] = "TRUE" if r["Recurring"] else "FALSE"
            desc.append("Recurring changed")
        if changes:
            s_ = d(changes.get("start", o["Start"]))
            e_ = d(changes.get("end", o["End"]))
            if s_ and e_ and e_ < s_:
                st.error(f"{o['Ref']}: the end date is before the start date. Nothing was saved.")
                return
            changes.update(id=r["id"], updated_at=now_iso(), updated_by=st.session_state.get("who", ""))
            rows.append(changes)
            logs.append((o["Ref"], o["Sub-action"], " · ".join(desc)))
    if not rows:
        st.info("No changes to save.")
        return
    write("tasks", rows)
    write("log", [{"id": uuid.uuid4().hex[:12], "project": pid, "ref": ref, "label": lab[:200], "change": ch[:500],
                   "by": st.session_state.get("who", ""), "at": now_iso()} for ref, lab, ch in logs])
    snapshot(pid)
    st.toast(f"Saved {len(rows)} sub-action{'s' if len(rows) != 1 else ''}")
    st.rerun()


# ================================================================ INDICATORS
def page_indicators():
    st.title("Key indicators")
    st.caption("Targets are the proposed figures from the inception report (subject to UNDP agreement). Enter achieved figures as results come in.")
    X = I.assign(s=I["seq"].map(num)).sort_values("s")
    E = pd.DataFrame({"id": X["id"], "Area": X["group"], "Indicator": X["name"], "Target": X["target"].map(num),
                      "Achieved": X["actual"].map(num), "Note": X["note"]})
    E["% of target"] = [min(100.0, a / t * 100) if t else None for a, t in zip(E.Achieved, E.Target)]
    ed = st.data_editor(E, hide_index=True, width="stretch", num_rows="dynamic" if EDITOR else "fixed",
                        disabled=["% of target"] if EDITOR else True,
                        column_config={"id": None, "% of target": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
                                       "Indicator": st.column_config.TextColumn(width="large"), "Note": st.column_config.TextColumn(width="large")},
                        key=f"ind_{pid}")
    if EDITOR and st.button("Save indicators", type="primary"):
        old = E.set_index("id")
        rows, logs = [], []
        nseq = int(X.s.max()) if not X.empty else 0
        for _, r in ed.iterrows():
            if pd.isna(r["Indicator"]) or not str(r["Indicator"]).strip():
                continue
            if pd.isna(r["id"]) or r["id"] not in old.index:
                nseq += 1
                rows.append({"id": f"{pid}-i{nseq:02d}-{uuid.uuid4().hex[:3]}", "project": pid, "seq": nseq, "group": r["Area"] or "",
                             "name": r["Indicator"], "unit": "", "target": num(r["Target"]), "actual": num(r["Achieved"]),
                             "note": r["Note"] or "", "updated_at": now_iso(), "updated_by": st.session_state.get("who", "")})
                logs.append(("", r["Indicator"], "Indicator added"))
                continue
            o = old.loc[r["id"]]
            ch = {}
            for col, key in [("Area", "group"), ("Indicator", "name"), ("Note", "note")]:
                v = "" if pd.isna(r[col]) else str(r[col])
                if v != str(o[col]):
                    ch[key] = v
            for col, key in [("Target", "target"), ("Achieved", "actual")]:
                if num(r[col]) != num(o[col]):
                    ch[key] = num(r[col])
            if ch:
                ch.update(id=r["id"], updated_at=now_iso(), updated_by=st.session_state.get("who", ""))
                rows.append(ch)
                logs.append(("", r["Indicator"], f"Achieved {num(o['Achieved']):g} → {num(r['Achieved']):g}" if "actual" in ch else "Indicator details changed"))
        gone = [i for i in old.index if i not in set(ed["id"].dropna())]
        if not rows and not gone:
            st.info("No changes to save.")
            return
        write("indicators", rows)
        remove("indicators", gone)
        for ref, lab, ch in logs:
            log(pid, ref, lab, ch)
        st.toast("Indicators saved")
        st.rerun()


# ================================================================ LOG
def page_log():
    st.title("Change log")
    if L.empty:
        st.caption("No updates yet.")
        return
    show = L[["at", "by", "ref", "label", "change"]].copy()
    show["at"] = pd.to_datetime(show["at"], errors="coerce", utc=True).dt.strftime("%d %b %Y %H:%M").str.lstrip("0")
    st.dataframe(show.rename(columns={"at": "When (UTC)", "by": "Who", "ref": "Ref", "label": "Item", "change": "Change"}),
                 hide_index=True, width="stretch", height=600)


# ================================================================ PROJECTS
def page_projects():
    st.title("ACE projects")
    st.dataframe(projects[["short", "name", "code", "client", "start", "end"]].rename(
        columns={"short": "Short name", "name": "Project", "code": "Ref", "client": "Client", "start": "Start", "end": "End"}),
        hide_index=True, width="stretch")
    if not EDITOR:
        return
    with st.expander("Edit the selected project's details"):
        project_form(P.to_dict() | {"id": pid}, "edit")
    with st.expander("Add a new project", expanded=False):
        st.markdown("Upload the project's DIP as an Excel or CSV file with these columns, in this order: "
                    "**No, Action, Sub-action, Responsible, Start date, End date, Status, Remarks**. "
                    "Leave No and Action blank on follow-on rows of the same action (merged cells are fine).")
        project_form({}, "new")


def project_form(p: dict, mode: str):
    with st.form(f"proj_{mode}"):
        c = st.columns(2)
        name = c[0].text_input("Full project name", p.get("name", ""))
        short = c[1].text_input("Short name", p.get("short", ""))
        code = c[0].text_input("Reference / contract no.", p.get("code", ""))
        client = c[1].text_input("Client / donor", p.get("client", ""))
        partners = c[0].text_input("Implemented by", p.get("partners", ""))
        baseline = c[1].text_input("Plan baseline (e.g. DIP 7 Oct 2026)", p.get("plan_baseline", ""))
        s = c[0].date_input("Start date", d(p.get("start")) or TODAY)
        e = c[1].date_input("End date", d(p.get("end")) or TODAY + timedelta(days=140))
        ws = c[0].date_input("Week 1 starts on", d(p.get("week_start")) or s)
        wk = c[1].number_input("Number of contract weeks", 0, 520, int(num(p.get("weeks", 0))))
        summary = st.text_area("Summary", p.get("summary", ""))
        up = st.file_uploader("Implementation plan (Excel or CSV)", type=["xlsx", "xls", "csv"]) if mode == "new" else None
        if st.form_submit_button("Save project" if mode == "edit" else "Create project", type="primary"):
            if not name.strip():
                st.error("Give the project a name.")
                return
            rec = {"name": name.strip(), "short": short.strip(), "code": code.strip(), "client": client.strip(),
                   "partners": partners.strip(), "start": s.isoformat(), "end": e.isoformat(), "week_start": ws.isoformat(),
                   "weeks": int(wk), "plan_baseline": baseline.strip(), "summary": summary.strip()}
            if mode == "edit":
                write("projects", [rec | {"id": p["id"]}])
                st.success("Saved.")
                st.rerun()
            new_id = re.sub(r"[^a-z0-9]+", "-", (short or name).lower()).strip("-")[:40] + "-" + uuid.uuid4().hex[:3]
            write("projects", [rec | {"id": new_id, "order": len(projects) + 1}])
            n = import_plan(new_id, up) if up else 0
            st.session_state["_next_pid"] = new_id
            st.success(f"Project created{f' with {n} sub-actions' if n else ''}.")
            st.rerun()


def import_plan(new_id: str, up) -> int:
    df = pd.read_csv(up, dtype=str, header=None) if up.name.lower().endswith(".csv") else pd.read_excel(up, dtype=str, header=None)
    df = df.fillna("")
    while df.shape[1] < 8:
        df[df.shape[1]] = ""
    no, seq, tasks, acts = 0, 0, [], {}
    for _, r in df.iterrows():
        c = [str(x).strip() for x in r.tolist()[:8]]
        if c[0].lower().rstrip(".") == "no" or (not any(c[1:4]) and not c[0].isdigit()):
            continue
        if re.match(r"^\d+(\.0)?$", c[0]):
            no, seq = int(float(c[0])), 0
        elif not no:
            no = 1
        if c[1]:
            acts.setdefault(no, {"title": c[1], "remarks": ""})
        if c[7]:
            acts.setdefault(no, {"title": f"Action {no}", "remarks": ""})
            acts[no]["remarks"] = (acts[no]["remarks"] + " " + c[7]).strip()
        if not c[2]:
            continue
        seq += 1
        s_ = d(c[4]) or TODAY
        e_ = d(c[5]) or s_
        sl = c[6].lower()
        status, pr = ("Completed", 100) if re.search(r"complet|approved|done", sl) and not re.search(r"pending|progress", sl) else \
            ("In progress", 50) if re.search(r"progress|submit|pending", sl) else ("Ongoing", 0) if re.search(r"ongoing|weekly|throughout", sl) else \
            ("Delayed", 0) if "delay" in sl else ("Not started", 0)
        tasks.append({"id": f"{new_id}-{no:02d}-{seq}", "project": new_id, "no": no, "seq": seq, "sub": c[2], "owner": c[3],
                      "due": "" if d(c[4]) else c[4], "start": s_.isoformat(), "end": e_.isoformat(), "status": status, "progress": pr,
                      "actual_end": "", "note": "", "recurring": "TRUE" if status == "Ongoing" else "FALSE", "milestone": "",
                      "updated_at": now_iso(), "updated_by": st.session_state.get("who", "")})
    write("tasks", tasks)
    write("actions", [{"id": f"{new_id}-{n:02d}", "project": new_id, "no": n, "title": a["title"], "remarks": a["remarks"]} for n, a in acts.items()])
    return len(tasks)


{"Overview": page_overview, "Implementation plan": page_plan, "Update progress": page_update,
 "Indicators": page_indicators, "Change log": page_log, "Projects": page_projects}[page]()
