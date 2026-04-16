#!/usr/bin/env python3
"""
Daily Brief Agent — runs in GitHub Actions
Consolidated: daily brief (calendar, Gmail, school, focus plan) +
              fresh job postings (Indeed RSS, Remotive, We Work Remotely)
Uses Anthropic API + Gmail API + Calendar API. One email to rule them all.
"""

import os, json, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── Credentials ────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
EMAILJS_SERVICE_ID   = "service_zvlvojn"
EMAILJS_TEMPLATE_ID  = "template_lya8tay"
EMAILJS_USER_ID      = "U14aXalSqzq7F0qpc"
EMAILJS_ACCESS_TOKEN = "ipcbTPz7PYwWZ3ESWa90w"

PAC      = timezone(timedelta(hours=-7))
now      = datetime.now(PAC)
DATE_STR = now.strftime("%A, %B %d, %Y")
cutoff   = datetime.now(timezone.utc) - timedelta(hours=36)

# ── Build Google API clients ────────────────────────────────────────────────────
def get_google_credentials():
    creds = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
        ],
    )
    creds.refresh(Request())
    return creds

creds = get_google_credentials()
gmail = build("gmail",    "v1", credentials=creds)
gcal  = build("calendar", "v3", credentials=creds)

# ── Job fetching (runs before Claude, passed in as context) ────────────────────
def fetch_jobs() -> list[dict]:
    jobs = []
    seen = set()

    def add(title, company, link, source, pub=""):
        if not link or link in seen:
            return
        seen.add(link)
        jobs.append({"title": title, "company": company, "link": link, "source": source, "pub": pub})

    # Indeed RSS
    def fetch_indeed(query, location, label):
        params = {"q": query, "l": location, "sort": "date", "fromage": "1", "limit": "10"}
        url = "https://www.indeed.com/rss?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
            channel = root.find("channel")
            if not channel:
                return
            for item in channel.findall("item")[:8]:
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link")  or "").strip()
                pub   = (item.findtext("pubDate") or "")
                company = ""
                if " - " in title:
                    parts = title.rsplit(" - ", 1)
                    title, company = parts[0].strip(), parts[1].strip()
                try:
                    pub_dt = parsedate_to_datetime(pub)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
                add(title, company, link, label, pub[:16])
        except Exception as e:
            print(f"  [Indeed] {label}: {e}")

    fetch_indeed("senior product manager machine learning",          "Seattle, WA", "Indeed · Seattle ML")
    fetch_indeed("senior product manager AI LLM generative",         "Remote",      "Indeed · Remote AI/LLM")
    fetch_indeed("senior product manager adtech marketing bidding",   "Remote",      "Indeed · Remote AdTech")
    fetch_indeed("product manager ROAS campaign optimization",        "Remote",      "Indeed · Remote ROAS")
    fetch_indeed("group product manager machine learning advertising","United States","Indeed · US ML Ads")

    # Remotive
    def fetch_remotive(search, label):
        url = "https://remotive.com/api/remote-jobs?category=product&search=" + urllib.parse.quote(search) + "&limit=15"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            for job in data.get("jobs", []):
                pub = job.get("publication_date", "")
                try:
                    pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass
                add(job.get("title",""), job.get("company_name",""), job.get("url",""), label, pub[:10])
        except Exception as e:
            print(f"  [Remotive] {label}: {e}")

    fetch_remotive("product manager machine learning", "Remotive · PM × ML")
    fetch_remotive("product manager AI LLM",           "Remotive · PM × AI/LLM")
    fetch_remotive("product manager adtech bidding",   "Remotive · PM × AdTech")

    # We Work Remotely
    try:
        req = urllib.request.Request(
            "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            root = ET.fromstring(r.read())
        for item in root.findall(".//item")[:10]:
            title   = (item.findtext("title")   or "").strip()
            link    = (item.findtext("link")    or "").strip()
            pub     = (item.findtext("pubDate") or "")
            company = ""
            if ":" in title:
                parts = title.split(":", 1)
                company, title = parts[0].strip(), parts[1].strip()
            if not any(k in title.lower() for k in ["product manager", " pm ", "pm,"]):
                continue
            try:
                pub_dt = parsedate_to_datetime(pub)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            except Exception:
                pass
            if link and link.startswith("/"):
                link = "https://weworkremotely.com" + link
            add(title, company, link, "We Work Remotely", pub[:16])
    except Exception as e:
        print(f"  [WWR]: {e}")

    return jobs

# ── Gmail / Calendar tool implementations ─────────────────────────────────────
def gmail_search(query: str, max_results: int = 20) -> list[dict]:
    result = gmail.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    out = []
    for m in result.get("messages", []):
        msg = gmail.users().messages().get(userId="me", id=m["id"], format="metadata",
              metadataHeaders=["From","Subject","Date"]).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        out.append({
            "id": m["id"], "threadId": msg["threadId"],
            "from": headers.get("From",""), "subject": headers.get("Subject",""),
            "date": headers.get("Date",""), "snippet": msg.get("snippet",""),
        })
    return out

def gmail_read(message_id: str) -> dict:
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    def extract_body(payload):
        if payload.get("body", {}).get("data"):
            import base64
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            body = extract_body(part)
            if body:
                return body
        return ""
    return {
        "id": message_id, "from": headers.get("From",""),
        "subject": headers.get("Subject",""), "date": headers.get("Date",""),
        "body": extract_body(msg["payload"])[:3000],
    }

def calendar_list_events(time_min: str, time_max: str) -> list[dict]:
    result = gcal.events().list(
        calendarId="primary", timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy="startTime", maxResults=20,
    ).execute()
    out = []
    for e in result.get("items", []):
        start = e.get("start", {})
        end   = e.get("end",   {})
        out.append({
            "summary":     e.get("summary",""),
            "start":       start.get("dateTime", start.get("date","")),
            "end":         end.get("dateTime",   end.get("date","")),
            "location":    e.get("location",""),
            "hangoutLink": e.get("hangoutLink",""),
            "description": (e.get("description","") or "")[:500],
        })
    return out

def send_emailjs(page: str, message: str) -> str:
    payload = {
        "service_id":  EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id":     EMAILJS_USER_ID,
        "accessToken": EMAILJS_ACCESS_TOKEN,
        "template_params": {"page": page, "message": message},
    }
    req = urllib.request.Request(
        "https://api.emailjs.com/api/v1.0/email/send",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()

# ── Tool definitions for Claude ─────────────────────────────────────────────────
TOOLS = [
    {
        "name": "gmail_search",
        "description": "Search Gmail messages. Returns sender, subject, date, snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_read_message",
        "description": "Read the full body of a Gmail message by ID.",
        "input_schema": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "List Google Calendar events between two ISO 8601 timestamps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
            },
            "required": ["time_min", "time_max"],
        },
    },
    {
        "name": "send_email",
        "description": "Send the completed HTML daily brief to Rahul's inbox via EmailJS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject":   {"type": "string"},
                "html_body": {"type": "string"},
            },
            "required": ["subject", "html_body"],
        },
    },
]

# ── Tool executor ────────────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "gmail_search":
            return json.dumps(gmail_search(inputs["query"], inputs.get("max_results", 20)))
        elif name == "gmail_read_message":
            return json.dumps(gmail_read(inputs["message_id"]))
        elif name == "calendar_list_events":
            return json.dumps(calendar_list_events(inputs["time_min"], inputs["time_max"]))
        elif name == "send_email":
            status = send_emailjs(inputs["subject"], inputs["html_body"])
            return f"Email sent. EmailJS: {status}"
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"

# ── Main ─────────────────────────────────────────────────────────────────────────
def run():
    # Fetch fresh job postings BEFORE starting Claude (no tool call needed)
    print("🔍 Fetching fresh job postings...")
    fresh_jobs = fetch_jobs()
    print(f"  Found {len(fresh_jobs)} fresh job postings")

    job_context = ""
    if fresh_jobs:
        lines = [f"Fresh job postings fetched from job boards (last 36h) — {len(fresh_jobs)} total:\n"]
        for i, j in enumerate(fresh_jobs, 1):
            lines.append(f"{i}. {j['title']} · {j['company']}")
            lines.append(f"   Source: {j['source']} | Posted: {j['pub']}")
            lines.append(f"   Apply: {j['link']}")
        job_context = "\n".join(lines)
    else:
        job_context = "No fresh job postings found today from job boards (slow day — suggest checking manually)."

    system_prompt = f"""You are Rahul's personal daily AI assistant. Today is {DATE_STR}. Rahul's email is bholerahul10@gmail.com.

Your job: generate ONE consolidated HTML daily brief that covers the whole morning — calendar, Gmail, job pipeline, school, AND fresh job postings — then send it to Rahul's inbox.

## Rahul's Profile (for scoring job fit)
Sr. PM at Expedia Group — ML bidding, ROAS, AdTech, A/B experimentation, auction systems, $1B+ ad spend. 8+ years. Based in Bothell WA. Open to Seattle hybrid or fully remote.
HIGH-FIT roles: ML PM, AI PM, Bidding PM, AdTech PM, Experimentation PM, Ads Platform PM.
Target companies: Google, Meta, Amazon, Microsoft, Booking, Airbnb, OpenAI, Anthropic, Databricks, Criteo, Trade Desk.

## Step 1 — Calendar
Use calendar_list_events for today (00:00–23:59 Pacific, UTC-7) and tomorrow's first 3 events.
Find the best focus window. Flag any work vs. personal calendar conflicts.

## Step 2 — School Updates
Search Gmail: from:nsd.org newer_than:14d
Also: "Northshore School District" newer_than:14d

## Step 3 — Smart Gmail Scan
Search: is:unread category:primary newer_than:3d -from:(noreply OR no-reply OR newsletter)
SKIP newsletters, promotions, automated senders. Max 5 real-person emails with urgent action items.

## Step 4 — Job Pipeline (existing applications via Gmail)
1. from:(greenhouse.io OR lever.co OR workday.com OR icims.com OR taleo.net OR jobvite.com OR ashbyhq.com) newer_than:2d
2. subject:(interview OR "next steps" OR "phone screen" OR "move forward" OR rejection) newer_than:3d category:primary
3. from:(google.com OR meta.com OR amazon.com OR microsoft.com OR openai.com OR anthropic.com OR databricks.com OR netflix.com OR uber.com) newer_than:3d

Classify each: Interview/Next Step | Application Received | Rejection | Real Recruiter
Pipeline summary table (always include):
| Status | Count |
| 🟢 Active (no rejection) | N |
| 📨 Received (last 48h) | N |
| 🎯 Interviews / Next Steps | N |
| 👤 Real Recruiter Threads | N |
| ❌ Rejections (last 48h) | N |

## Step 5 — Fresh Job Postings (pre-fetched — include ALL of these)
The following jobs were already fetched from job boards. Score each HIGH/MEDIUM fit based on Rahul's profile and include in the email.
HIGH = ML/AI/Bidding/AdTech/ROAS/Experimentation PM at matching seniority.
MEDIUM = Adjacent roles (growth, data, marketplace) at strong companies.
Show HIGH jobs first, then MEDIUM. Skip LOW.

{job_context}

## Step 6 — Ms. Lund's Class
Search: from:dorothy.lund newer_than:30d
Search: from:nsd.org newer_than:14d

## Step 7 — Build & Send HTML Email
Build ONE consolidated HTML email. Table-based layout only (no flexbox/grid — Gmail strips them). All CSS inline. Max width 600px.

Section order:
1. 🌅 Header — dark navy (#1e3a5f), date in #93c5fd, greeting, 2-sentence morning note
2. 📊 Stats row — 3 white cards: meetings count, action items, best focus window
3. 📅 Schedule — time badges colored by type: purple #ede9fe=WORK, amber=interview, blue=personal, green=focus block. Include WORK/PERSONAL badge labels.
4. 🎯 Job Pipeline — pipeline summary table (navy header) + individual cards (amber=interview, green=recruiter, red=rejection)
5. 💼 Fresh Job Postings Today — NEW section: cards for HIGH fit jobs (amber border), MEDIUM fit (blue border). Each card: title, company, fit reason, apply link. Sub-header: "N new postings · Sources: Indeed · Remotive · We Work Remotely"
6. 📬 Action Items — colored left-border rows (red=urgent, amber=this week)
7. 👩‍🏫 Ms. Lund's Class — sky-blue left border
8. 🚀 Focus Plan — dark green (#064e3b), numbered priorities (include top job to apply to tonight)
9. 👀 Tomorrow Preview

After building the HTML, call send_email:
- subject: "🌅 Daily Brief + 💼 Job Postings — {DATE_STR}"
- html_body: full HTML string

## Rules
- Table-based layout only. All styles inline.
- NEVER skip the Job Pipeline or Fresh Job Postings sections.
- For Fresh Job Postings: only use the URLs provided above — never fabricate links.
- If Ms. Lund section is empty, say so.
- Do not ask for clarification.
"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": f"Run my consolidated daily brief + job postings for {DATE_STR}."}]

    print(f"🌅 Starting consolidated daily brief for {DATE_STR}...")

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        print(f"  → stop_reason: {response.stop_reason}")
        for block in response.content:
            if hasattr(block, "text"):
                print(f"  Claude: {block.text[:200]}")

        if response.stop_reason == "end_turn":
            print("✅ Consolidated daily brief sent.")
            break

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for tu in tool_uses:
            print(f"  🔧 {tu.name}({list(tu.input.keys())})")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": execute_tool(tu.name, tu.input),
            })
        messages.append({"role": "user", "content": tool_results})

if __name__ == "__main__":
    run()
