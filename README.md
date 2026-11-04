# ACE Project Tracker (Streamlit)

Project officers update their Detailed Implementation Plan (DIP); executives follow progress against plan.
All data lives in one Google Sheet, so nothing is lost when Streamlit restarts the app.

Views: Overview · Implementation plan (Gantt + Excel download) · Update progress · Indicators · Change log · Projects.

## 1. Try it on your computer (optional)

```
pip install -r requirements.txt
streamlit run app.py
```
The app requires Google Sheets credentials by default. For an offline PowerShell test,
set `$env:ACE_TRACKER_LOCAL = "1"` before starting Streamlit with no Google Sheets
credentials configured. This mode stores data in `data/*.csv`, initialized from
`seed_data/` (UNDP A2F, DIP 7 Oct 2026). Leave this variable unset on Community Cloud.

## 2. Create the Google Sheet and a service account (once, ~10 minutes)

1. Go to <https://console.cloud.google.com>, create a project (e.g. "ACE Tracker").
2. **APIs & Services → Library**: enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**. Name it `ace-tracker`, finish.
4. Open the service account → **Keys → Add key → JSON**. A `.json` file downloads; keep it private.
5. Create a new, empty Google Sheet (e.g. "ACE Tracker data").
6. Click **Share** on the sheet and add the service account's `client_email` (from the JSON) as **Editor**.
7. Copy the sheet ID from its link: `docs.google.com/spreadsheets/d/<SHEET ID>/edit`.

The app creates the tabs it needs (`projects, actions, tasks, indicators, log, snaps`) on first run.
Alternatively, upload `ACE_Tracker_starter_sheet.xlsx` to Google Drive and open it as a Google Sheet.

## 3. Publish on Streamlit Community Cloud

1. Put this folder in a GitHub repository (private is fine). Do **not** upload a real `secrets.toml`.
2. Go to <https://share.streamlit.io> → **Create app** → choose the repo, branch, and `app.py`.
3. **Advanced settings → Secrets**: paste the contents of `.streamlit/secrets.toml` with your real values
   (two passwords, the sheet ID, and every field from the service-account JSON).
4. Deploy. Open the app, sign in with the **editor** password, and click
   **Load the UNDP A2F plan** (only shown while the sheet is empty).
5. Share the app link. Executives use the viewer password; project officers use the editor password and type their name,
   which is recorded in the change log.

## How progress is calculated

- **Actual progress** = average % complete of all non-recurring sub-actions (equal weight).
- **Planned by today** = average share of each sub-action's planned date range that has elapsed.
- **Overdue** = planned end date passed and not Completed. **Late start** = start date passed 3+ days ago and still Not started.
- Recurring items (bi-weekly meetings, weekly tracker, safeguarding) show as Ongoing and are excluded from %.
- Each save records a daily snapshot, which draws the actual line on the progress curve.

## Adding another project

Projects → **Add a new project**, fill in dates, and upload its DIP as Excel/CSV with columns:
`No, Action, Sub-action, Responsible, Start date, End date, Status, Remarks` (merged No/Action cells are fine).

## Editing the Google Sheet directly

You can, but keep the header row and the `id` column unchanged. Dates are `YYYY-MM-DD`.
Changes appear in the app within a minute, or immediately after **Refresh data**.
