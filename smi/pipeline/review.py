"""Spot-check articles: is this the right general type of story?

    python pipeline/run.py review          # opens http://127.0.0.1:8765

Review is optional: Claude's coding decides what goes into incidents, and
this page is for checking its work. Serves a page on this machine only.
Each article shows its source link, the mental-health quote, the quote
behind every signal, its incident and how the incident used it.

  Accept  -> looked at, and the right general type. Changes nothing else.
  Reject  -> stays in articles.csv as rejected and is never coded; from the
             next incidents rebuild it is out of its incident (and an
             incident left with no articles is removed).
  Flag    -> stays for a second look.

Fields can still be corrected and saved, and those values are checked
against the codebook, but none is required. The page holds the articles in
memory: stop it before running a harvest, or a later click would overwrite
the new rows.
"""

import json
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
from config import (INCIDENT_FORMS, MIN_AGE, OTHER_PARTIES, REVIEW_STATUSES, SIGNAL_FIELDS,
                    SUBJECT_ROLES, US_STATES)
from dedupe import _split_evidence

TEXT_FIELDS = ["subject_name", "age", "sex", "city", "state", "incident_date", "subject_role",
               "other_party", "incident_form", "diagnoses", "days_since_release", "summary",
               "review_note"]
BOOL_FIELDS = ["fatality", "violence", "killed_by_police", *SIGNAL_FIELDS]


def _apply_edits(row: dict, edits: dict) -> None:
    for f in TEXT_FIELDS:
        if f in edits:
            v = str(edits[f] or "").strip()
            row[f] = v.upper() if f in ("state", "sex") else v
    evidence = _split_evidence(row.get("signal_evidence", ""))
    for f in BOOL_FIELDS:
        if f in edits:
            row[f] = "true" if edits[f] in (True, "true", "on", "1") else "false"
            if f in SIGNAL_FIELDS:
                if row[f] == "true":
                    evidence.setdefault(f, "(set by reviewer)")
                else:
                    evidence.pop(f, None)
    row["signal_evidence"] = " || ".join(f"{f}: {q}" for f, q in evidence.items())
    row["subject_key"] = store.subject_key(row.get("subject_name"), row.get("state"))


def check_values(row: dict) -> str | None:
    """A problem with a value the reviewer entered, or None. Blank is fine."""
    if row.get("subject_role") and row["subject_role"] not in SUBJECT_ROLES:
        return f"subject_role {row['subject_role']!r} is not in the codebook"
    age = row.get("age") or ""
    if age:
        if not age.isdigit():
            return "age must be a whole number"
        if int(age) < MIN_AGE:
            return f"subject is under {MIN_AGE}: excluded"
    if row.get("state") and row["state"] not in US_STATES:
        return f"{row['state']!r} is not a US state"
    if row.get("other_party") and row["other_party"] not in OTHER_PARTIES:
        return f"other_party {row['other_party']!r} is not in the codebook"
    if row.get("incident_form") and row["incident_form"] not in INCIDENT_FORMS:
        return f"incident_form {row['incident_form']!r} is not in the codebook"
    if row.get("sex") and row["sex"] not in ("M", "F", "U"):
        return "sex must be M, F or U"
    if row.get("days_since_release") and not row["days_since_release"].isdigit():
        return "days_since_release must be a whole number"
    return None


def apply_review(row_id: str, status: str | None, edits: dict,
                 articles: list[dict]) -> tuple[bool, str]:
    """Apply a reviewer's edits and decision to one article. Returns
    (ok, message). On a bad value nothing is saved."""
    row = next((r for r in articles if r["id"] == row_id), None)
    if row is None:
        return False, f"no article {row_id}"
    if status is not None and status not in REVIEW_STATUSES:
        return False, f"unknown status {status!r}"
    edited = dict(row)
    _apply_edits(edited, edits or {})
    problem = check_values(edited)
    if problem:
        return False, f"Not saved: {problem}."
    row.update(edited)
    if status is not None:
        row["review_status"] = status
        row["reviewed_date"] = date.today().isoformat()
        return True, f"{row_id} marked {status}"
    return True, f"{row_id} saved"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SMI article review</title><style>
:root{--ink:#1d2130;--muted:#5d6375;--rule:#d5d9e6;--soft:#f3f5fb;--accent:#2c48dc;
--ok:#16794a;--bad:#b42318;--warn:#b54708;--bg:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Arial,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:3px solid var(--accent);padding:12px 18px}
h1{margin:0;font-size:19px}
.lede{margin:2px 0 0;color:var(--muted);font-size:13px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.tabs button{border:1px solid var(--rule);background:var(--soft);padding:4px 12px;border-radius:14px;cursor:pointer;font:inherit;font-size:13px}
.tabs button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.wrap{max-width:960px;margin:0 auto;padding:18px}
.card{border:1px solid var(--rule);border-radius:8px;margin-bottom:18px;overflow:hidden}
.hd{background:var(--soft);padding:10px 14px;border-bottom:1px solid var(--rule)}
.hd .t{font-weight:700}
.hd .s{color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:1px 7px;border-radius:9px;margin-right:6px;background:#e4e8f7;color:var(--accent)}
.badge.pb{background:#fdf0e1;color:var(--warn)}
.excl{background:#fdecea;border-left:4px solid var(--bad);padding:6px 10px;margin:8px 0;font-size:13px}
.bd{padding:12px 14px}
.q{background:#f8f9fc;border-left:3px solid var(--rule);padding:6px 10px;margin:6px 0;font-size:13.5px}
.q b{font-size:12px;color:var(--muted)}
details{margin-top:8px}
summary{cursor:pointer;color:var(--accent);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px 12px;margin:10px 0}
label{font-size:12px;color:var(--muted);display:block}
input[type=text],select,textarea{width:100%;font:inherit;font-size:14px;padding:4px 6px;border:1px solid var(--rule);border-radius:4px}
textarea{min-height:56px}
.checks{display:flex;flex-wrap:wrap;gap:4px 14px;margin:8px 0}
.checks label{display:flex;align-items:center;gap:4px;font-size:13px;color:var(--ink)}
.acts{display:flex;gap:8px;flex-wrap:wrap;padding:10px 14px;border-top:1px solid var(--rule)}
.acts button{font:inherit;padding:6px 14px;border-radius:5px;border:1px solid var(--rule);background:#fff;cursor:pointer}
.acts .ok{background:var(--ok);color:#fff;border-color:var(--ok)}
.acts .bad{color:var(--bad);border-color:var(--bad)}
.acts .warn{color:var(--warn);border-color:var(--warn)}
.msg{font-size:13px;align-self:center;color:var(--muted)}
.msg.err{color:var(--bad)}
.trig{font:12px ui-monospace,Consolas,monospace;color:var(--muted);word-break:break-word}
.empty{color:var(--muted);padding:30px 0;text-align:center}
@media (max-width:600px){.wrap{padding:12px}}
</style></head><body>
<header><h1>SMI article review</h1>
<p class="lede">A spot-check of Claude's work. Accept means the article is the right general type of story; Reject keeps it out of its incident from the next rebuild. Nothing needs filling in.</p>
<div class="tabs" id="tabs"></div></header>
<div class="wrap" id="list"></div>
<script>
const C = __CODEBOOK__;
let ROWS = [], VIEW = "pending";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const opt = (vals, cur) => '<option value=""></option>' + vals.map(v => `<option${v===cur?" selected":""}>${esc(v)}</option>`).join("");
const txt = (r, f, label) => `<div><label>${label}</label><input type="text" data-f="${f}" value="${esc(r[f])}"></div>`;
const sel = (r, f, label, vals) => `<div><label>${label}</label><select data-f="${f}">${opt(vals, r[f])}</select></div>`;

async function load(){ ROWS = await (await fetch("/rows")).json(); render(); }

function render(){
  const counts = {}; C.statuses.forEach(s => counts[s] = 0); ROWS.forEach(r => counts[r.review_status] = (counts[r.review_status]||0)+1);
  document.getElementById("tabs").innerHTML = C.statuses.map(s =>
    `<button class="${s===VIEW?"on":""}" onclick="VIEW='${s}';render()">${s} (${counts[s]||0})</button>`).join("");
  const rows = ROWS.filter(r => r.review_status === VIEW);
  document.getElementById("list").innerHTML = rows.length ? rows.map(card).join("") : '<div class="empty">Nothing here.</div>';
}

function card(r){
  const ev = (r.signal_evidence||"").split(" || ").filter(Boolean).map(e => {
    const i = e.indexOf(": "); return `<div class="q"><b>${esc(e.slice(0,i))}</b><br>${esc(e.slice(i+2))}</div>`; }).join("");
  const checks = C.bools.map(f => `<label><input type="checkbox" data-f="${f}"${r[f]==="true"?" checked":""}>${esc(f)}</label>`).join("");
  return `<div class="card" id="c-${esc(r.id)}">
  <div class="hd"><div class="t">${esc(r.headline || r.summary)}</div>
   <div class="s"><span class="badge ${r.coded_by==="phrasebook"?"pb":""}">${esc(r.coded_by)}</span>${esc(r.id)} &middot; ${esc(r.published || r.incident_date)} &middot;
   <a href="${esc(r.source_url)}" target="_blank" rel="noopener">${esc(r.source_name||"source")}</a>
   ${r.incident_id ? " &middot; incident " + esc(r.incident_id) + (r.contribution ? " (" + esc(r.contribution) + ")" : "") : ""}
   ${r.repeat_offender_ids ? " &middot; repeat-offender ids: " + esc(r.repeat_offender_ids) : ""}</div>
   ${r.excluded_reason ? `<div class="excl">Claude excluded this article from incidents: ${esc(r.excluded_reason)}</div>` : ""}
  </div>
  <div class="bd">
   ${r.mh_evidence_quote ? `<div class="q"><b>mental-health indication</b><br>${esc(r.mh_evidence_quote)}</div>` : ""}
   ${ev}
   ${r.trigger_phrases ? `<p class="trig">phrase book: ${esc(r.trigger_phrases)}</p>` : ""}
   <details><summary>Correct fields (optional)</summary>
   <div class="grid">
    ${txt(r,"subject_name","Subject name")}${txt(r,"age","Age")}${sel(r,"sex","Sex",["M","F","U"])}
    ${txt(r,"city","City")}${txt(r,"state","State (2 letters)")}${txt(r,"incident_date","Incident date")}
    ${sel(r,"subject_role","Subject role",C.roles)}${sel(r,"other_party","Other party",C.parties)}
    ${sel(r,"incident_form","Incident form",C.forms)}${txt(r,"days_since_release","Days since release")}
    ${txt(r,"diagnoses","Diagnoses (named)")}
   </div>
   <div class="checks">${checks}</div>
   <label>Summary</label><textarea data-f="summary">${esc(r.summary)}</textarea>
   <label>Review note</label><textarea data-f="review_note">${esc(r.review_note)}</textarea>
   </details>
  </div>
  <div class="acts">
   <button class="ok" onclick="decide('${esc(r.id)}','accepted')">Accept</button>
   <button class="bad" onclick="decide('${esc(r.id)}','rejected')">Reject</button>
   <button class="warn" onclick="decide('${esc(r.id)}','flagged')">Flag</button>
   <button onclick="decide('${esc(r.id)}',null)">Save edits</button>
   <span class="msg" id="m-${esc(r.id)}"></span>
  </div></div>`;
}

async function decide(id, status){
  const el = document.getElementById("c-" + id), edits = {};
  el.querySelectorAll("[data-f]").forEach(i => edits[i.dataset.f] = i.type === "checkbox" ? i.checked : i.value);
  const res = await (await fetch("/review", {method:"POST", headers:{"Content-Type":"application/json"},
                                          body: JSON.stringify({id, status, edits})})).json();
  if (!res.ok){ const m = document.getElementById("m-" + id); m.textContent = res.message; m.className = "msg err"; return; }
  ROWS = res.rows; render();
}
load();
</script></body></html>"""


def _page() -> str:
    codebook = {"statuses": REVIEW_STATUSES, "roles": SUBJECT_ROLES, "parties": OTHER_PARTIES,
                "forms": INCIDENT_FORMS, "bools": BOOL_FIELDS}
    return PAGE.replace("__CODEBOOK__", json.dumps(codebook))


def serve(port: int = 8765, open_browser: bool = True) -> None:
    articles = store.load_articles()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, ctype: str = "text/html; charset=utf-8", code: int = 200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                return self._send(_page())
            if self.path == "/rows":
                return self._send(json.dumps(articles), "application/json")
            return self._send("not found", code=404)

        def do_POST(self):
            if self.path != "/review":
                return self._send("not found", code=404)
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            with lock:
                ok, message = apply_review(str(payload.get("id")), payload.get("status"),
                                           payload.get("edits") or {}, articles)
                if ok:
                    store.save_articles(articles)
            self._send(json.dumps({"ok": ok, "message": message, "rows": articles}), "application/json")

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    pending = sum(r.get("review_status") == "pending" for r in articles)
    print(f"Articles: {len(articles)}, {pending} pending review. Serving {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
