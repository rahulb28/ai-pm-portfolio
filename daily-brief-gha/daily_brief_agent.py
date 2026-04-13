#!/usr/bin/env python3
"""
Daily Brief Agent — runs in GitHub Actions
Uses Anthropic API + Gmail API + Calendar API to generate and email Rahul's daily brief.
Full Claude intelligence, no MCP needed.
"""

import os, json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

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

PAC = timezone(timedelta(hours=-7))
now = datetime.now(PAC)
DATE_STR = now.strftime("%A, %B %d, %Y")

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
gmail   = build("gmail",    "v1", credentials=creds)
gcal    = build("calendar", "v3", credentials=creds)

# ── Tool implementations ────────────────────────────────────────────────────────
def gmail_search(query: str, max_results: int = 20) -> list[dict]:
    result = gmail.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    messages = result.get("messages", [])
    out = []
    for m in messages:
        msg = gmail.users().messages().get(userId="me", id=m["id"], format="metadata",
              metadataHeaders=["From","Subject","Date"]).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        out.append({
            "id": m["id"],
            "threadId": msg["threadId"],
            "from": headers.get("From",""),
            "subject": headers.get("Subject",""),
            "date": headers.get("Date",""),
            "snippet": msg.get("snippet",""),
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
        "id": message_id,
        "from": headers.get("From",""),
        "subject": headers.get("Subject",""),
        "date": headers.get("Date",""),
        "body": extract_body(msg["payload"])[:3000],
    }

def calendar_list_events(time_min: str, time_max: str) -> list[dict]:
    result = gcal.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=20,
    ).execute()
    out = []
    for e in result.get("items", []):
        start = e.get("start", {})
        end   = e.get("end", {})
        out.append({
            "summary":  e.get("summary", ""),
            "start":    start.get("dateTime", start.get("date", "")),
            "end":      end.get("dateTime",   end.get("date", "")),
            "location": e.get("location",""),
            "hangoutLink": e.get("hangoutLink",""),
            "description": (e.get("description","") or "")[:500],
        })
    return out

def send_emailjs(page: str, message: str) -> str:
    payload = {
        "service_id":   EMAILJS_SERVICE_ID,
        "template_id":  EMAILJS_TEMPLATE_ID,
        "user_id":      EMAILJS_USER_ID,
        "accessToken":  EMAILJS_ACCESS_TOKEN,
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
        "description": "Search Gmail messages by query string. Returns list of messages with sender, subject, date, snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Gmail search query (same syntax as Gmail search bar)"},
                "max_results": {"type": "integer", "description": "Max messages to return (default 20)", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_read_message",
        "description": "Read the full body of a specific Gmail message by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The Gmail message ID"},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "List Google Calendar events between two timestamps (ISO 8601 with timezone).",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "Start time in ISO 8601 format (e.g. 2026-04-13T00:00:00-07:00)"},
                "time_max": {"type": "string", "description": "End time in ISO 8601 format"},
            },
            "required": ["time_min", "time_max"],
        },
    },
    {
        "name": "send_email",
        "description": "Send the completed HTML daily brief email to Rahul's inbox via EmailJS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Email subject line"},
                "html_body": {"type": "string", "description": "Full HTML email body"},
            },
            "required": ["subject", "html_body"],
        },
    },
]

# ── Tool executor ────────────────────────────────────────────────────────────────
def execute_tool(name: str, inputs: dict) -> str:
    try:
        if name == "gmail_search":
            result = gmail_search(inputs["query"], inputs.get("max_results", 20))
            return json.dumps(result)
        elif name == "gmail_read_message":
            result = gmail_read(inputs["message_id"])
            return json.dumps(result)
        elif name == "calendar_list_events":
            result = calendar_list_events(inputs["time_min"], inputs["time_max"])
            return json.dumps(result)
        elif name == "send_email":
            status = send_emailjs(inputs["subject"], inputs["html_body"])
            return f"Email sent successfully. EmailJS response: {status}"
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"

# ── System prompt ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""You are Rahul's personal daily AI assistant. Today is {DATE_STR}. Rahul's email is bholerahul10@gmail.com.

Your job: generate a beautiful, intelligent HTML daily brief and send it to Rahul's inbox.

## Step 1 — Calendar
Use calendar_list_events to fetch today's events (00:00–23:59 Pacific time, UTC-7).
Also fetch tomorrow's first 3 events.
Find the best focus window (largest gap between meetings).

## Step 2 — School Calendar
Search Gmail: from:nsd.org newer_than:14d
Also search: "Northshore School District" newer_than:14d

## Step 3 — Smart Gmail Scan
Search: is:unread category:primary newer_than:3d -from:(noreply OR no-reply OR newsletter)
SKIP: LinkedIn, newsletters, promotions, automated senders.
INCLUDE: real people, urgent keywords (deadline, action required, RSVP, follow up). Max 5.

## Step 4 — Job Pipeline
Run these searches:
1. from:(greenhouse.io OR lever.co OR workday.com OR icims.com OR taleo.net OR jobvite.com OR ashbyhq.com) newer_than:2d
2. subject:(interview OR "next steps" OR "phone screen" OR "move forward" OR rejection) newer_than:3d category:primary
3. from:(snapchat.com OR google.com OR meta.com OR amazon.com OR microsoft.com OR openai.com OR anthropic.com OR databricks.com OR netflix.com OR uber.com) newer_than:3d

Classify each as: Interview/Next Step | Application Received | Rejection | Real Recruiter
Count: active applications, new interviews (48h), rejections (48h), recruiter threads.

## Step 5 — Ms. Lund
Search: from:dorothy.lund newer_than:30d
Search: from:nsd.org newer_than:14d

## Step 6 — Build & Send HTML Email
Build a full HTML email using ONLY inline CSS and table-based layout (no flexbox, no CSS grid — Gmail strips those).

Design specs:
- Max width 600px, background #f1f5f9
- Header: background-color #1e3a5f (dark navy), white text, date in #93c5fd, morning note in #bfdbfe
- Stats row: 3 white cards (meetings count, action items count, best focus window)
- Schedule: white card, time badges in #dbeafe/#d1fae5 for meetings/focus blocks
- Job Pipeline: white card + summary TABLE with colored rows + individual job cards (amber for top fit, green for recruiter, red for rejection)
- Action Items: colored left-border rows (red #ef4444 = urgent, amber #f59e0b = this week)
- Ms. Lund: sky-blue left border (#0ea5e9), light blue background
- Focus Plan: dark green card (#064e3b), numbered priorities
- Footer: small gray text

Pipeline summary table format (always include):
| Status | Count |
|--------|-------|
| 🟢 Active (no rejection) | N |
| 📨 Received (last 48h) | N |
| 🎯 Interviews / Next Steps | N |
| 👤 Real Recruiter Threads | N |
| ❌ Rejections (last 48h) | N |

After building the HTML, call send_email with:
- subject: "🌅 Daily Brief — {DATE_STR}"
- html_body: the full HTML string

## Rules
- Use table-based layout only (email-safe HTML)
- All styles must be inline
- Emojis and bold are fine
- NEVER skip the Job Pipeline section
- If Ms. Lund section is empty, say so explicitly
- Do not ask for clarification — use best judgment
"""

# ── Agent loop ────────────────────────────────────────────────────────────────────
def run():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": f"Run my daily brief for {DATE_STR}. Fetch my calendar and Gmail, build the HTML brief, and send it to my inbox."}]

    print(f"🌅 Starting daily brief for {DATE_STR}...")

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        print(f"  → stop_reason: {response.stop_reason}")

        # Collect text output
        for block in response.content:
            if hasattr(block, "text"):
                print(f"  Claude: {block.text[:200]}")

        if response.stop_reason == "end_turn":
            print("✅ Daily brief complete.")
            break

        # Handle tool calls
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tu in tool_uses:
            print(f"  🔧 Tool: {tu.name}({list(tu.input.keys())})")
            result = execute_tool(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

if __name__ == "__main__":
    run()
