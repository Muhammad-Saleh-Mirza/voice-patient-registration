"""Bonus: a read-only HTML dashboard at /dashboard.

Deliberately a single server-rendered page with no build step, no framework and
no JavaScript dependencies. Its job is to let a reviewer confirm, in one glance
after hanging up the phone, that the record they just dictated is really in the
database. Anything fancier would be time spent away from the call experience.
"""

import html
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.database import get_db
from app.models import CallLog

router = APIRouter(tags=["dashboard"])

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.5rem; font:15px/1.5 -apple-system,BlinkMacSystemFont,
       "Segoe UI",Roboto,Helvetica,Arial,sans-serif; background:#f6f7f9; color:#16202c; }
@media (prefers-color-scheme: dark) { body { background:#11151b; color:#e6eaf0; } }
.wrap { max-width:1100px; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
.sub { color:#6b7787; margin:0 0 1.75rem; font-size:.9rem; }
.cards { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.75rem; }
.card { flex:1 1 160px; background:#fff; border:1px solid #e2e6ec; border-radius:10px;
        padding:1rem 1.1rem; }
@media (prefers-color-scheme: dark) { .card { background:#1a2029; border-color:#2a323d; } }
.card .n { font-size:1.75rem; font-weight:650; letter-spacing:-.02em; }
.card .l { font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; color:#6b7787; }
h2 { font-size:1rem; margin:2rem 0 .75rem; }
.scroll { overflow-x:auto; border:1px solid #e2e6ec; border-radius:10px; background:#fff; }
@media (prefers-color-scheme: dark) { .scroll { background:#1a2029; border-color:#2a323d; } }
table { border-collapse:collapse; width:100%; font-size:.86rem; }
th { text-align:left; font-weight:600; font-size:.72rem; text-transform:uppercase;
     letter-spacing:.06em; color:#6b7787; padding:.7rem .85rem;
     border-bottom:1px solid #e2e6ec; white-space:nowrap; }
td { padding:.7rem .85rem; border-bottom:1px solid #eef1f4; white-space:nowrap; }
@media (prefers-color-scheme: dark) { th,td { border-color:#2a323d; } }
tr:last-child td { border-bottom:none; }
.empty { padding:2rem; text-align:center; color:#6b7787; }
code { font:12px ui-monospace,SFMono-Regular,Menlo,monospace; color:#6b7787; }
"""


def _esc(value) -> str:
    """Escape everything rendered into the page. The data came in over a phone
    line from an untrusted caller, so it is treated as hostile."""
    return html.escape(str(value)) if value not in (None, "") else "—"


@router.get("/dashboard", response_class=HTMLResponse, summary="Registered patients (HTML)")
def dashboard(db: Session = Depends(get_db)):
    patients = services.list_patients(db, limit=200)
    logs = list(
        db.execute(select(CallLog).order_by(CallLog.created_at.desc()).limit(15))
        .scalars()
        .all()
    )
    today = datetime.now(timezone.utc).date()
    today_count = sum(1 for p in patients if p.created_at.date() == today)

    rows = "".join(
        f"<tr>"
        f"<td>{_esc(p.first_name)} {_esc(p.last_name)}</td>"
        f"<td>{_esc(p.date_of_birth.strftime('%m/%d/%Y'))}</td>"
        f"<td>{_esc(p.sex)}</td>"
        f"<td>{_esc(p.phone_number)}</td>"
        f"<td>{_esc(p.address_line_1)}, {_esc(p.city)}, {_esc(p.state)} {_esc(p.zip_code)}</td>"
        f"<td>{_esc(p.insurance_provider)}</td>"
        f"<td>{_esc(p.created_at.strftime('%Y-%m-%d %H:%M UTC'))}</td>"
        f"<td><code>{_esc(p.patient_id)}</code></td>"
        f"</tr>"
        for p in patients
    ) or '<tr><td colspan="8" class="empty">No patients yet — call the number to register one.</td></tr>'

    log_rows = "".join(
        f"<tr><td>{_esc(l.created_at.strftime('%Y-%m-%d %H:%M UTC'))}</td>"
        f"<td>{_esc(l.tool_name)}</td><td>{_esc(l.outcome)}</td>"
        f"<td>{_esc(l.caller_number)}</td></tr>"
        for l in logs
    ) or '<tr><td colspan="4" class="empty">No call activity yet.</td></tr>'

    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Patient Registrations</title><style>{_STYLE}</style></head>
<body><div class="wrap">
<h1>Patient Registrations</h1>
<p class="sub">Live view of records collected by the voice agent.</p>
<div class="cards">
  <div class="card"><div class="n">{len(patients)}</div><div class="l">Total patients</div></div>
  <div class="card"><div class="n">{today_count}</div><div class="l">Registered today</div></div>
  <div class="card"><div class="n">{len(logs)}</div><div class="l">Recent tool calls</div></div>
</div>
<h2>Patients</h2>
<div class="scroll"><table>
<thead><tr><th>Name</th><th>DOB</th><th>Sex</th><th>Phone</th><th>Address</th>
<th>Insurance</th><th>Registered</th><th>Patient ID</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<h2>Recent call activity</h2>
<div class="scroll"><table>
<thead><tr><th>Time</th><th>Tool</th><th>Outcome</th><th>Caller</th></tr></thead>
<tbody>{log_rows}</tbody></table></div>
</div></body></html>""")
