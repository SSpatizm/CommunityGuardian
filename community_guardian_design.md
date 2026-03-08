# Community Guardian — Design & Implementation Document

**Scenario:** #3 — Community Safety & Digital Wellness
**Target Audience:** Neighborhood Groups
**Candidate Role:** Palo Alto Networks FY26 IT / Software / Cybersecurity / Cloud
**Time Budget:** 4–6 hours
**Submission Deadline:** March 8, 2026, 5:00 PM PT

---

## 1. Problem Statement

As digital and physical security threats become more complex, individuals struggle to keep up with relevant safety information. Information is often scattered across news sites and social media, leading to alert fatigue or unnecessary anxiety without actionable steps. 

Community Guardian is a lightweight incident reporting and digest platform that lets neighborhood residents submit safety reports, automatically classifies them by category and severity using AI (with a deterministic fallback), and presents a filtered, searchable feed of actionable community safety intelligence.


---

## 2. Architecture Overview

Three fully decoupled layers. No layer reaches into another's concerns.

```
┌─────────────────────────────────────────────────────┐
│                   FLASK (Presentation)               │
│  Routes · Templates · Forms · Input Validation       │
│  Knows: HTTP, HTML, user input/output                │
│  Does NOT know: how classification works, SQL schema │
└──────────────────────┬──────────────────────────────┘
                       │ calls classify_report()
                       │ returns dict
┌──────────────────────▼──────────────────────────────┐
│              CLASSIFIER ADAPTER (Business Logic)     │
│  AI path · Rule engine fallback · Response validation│
│  Knows: text processing, API calls, keyword rules    │
│  Does NOT know: HTTP, database, templates            │
└──────────────────────┬──────────────────────────────┘
                       │ returns classification dict
┌──────────────────────▼──────────────────────────────┐
│                   SQLITE (Persistence)               │
│  Single table · Indexes · CRUD queries               │
│  Knows: rows, columns, SQL                           │
│  Does NOT know: classification, HTTP, business logic │
└─────────────────────────────────────────────────────┘
```

Supporting files (no runtime role):
- `rules.json` — keyword dictionaries and action lookup table, loaded into memory at startup
- `sample_data.json` — synthetic seed data, ingested once via seed script
- `.env` — API key and provider config, never committed

---

## 3. Data Model

### 3.1 Single Table: `incidents`

```sql
CREATE TABLE IF NOT EXISTS incidents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL,
    location         TEXT NOT NULL,
    reported_at      TEXT NOT NULL,
    created_at       TEXT NOT NULL,

    category         TEXT NOT NULL DEFAULT 'uncategorized',
    severity         TEXT NOT NULL DEFAULT 'informational',
    confidence       REAL NOT NULL DEFAULT 0.0,
    action           TEXT NOT NULL DEFAULT '',
    classified_by    TEXT NOT NULL DEFAULT 'pending',
    matched_keywords TEXT DEFAULT NULL,
    fallback_reason  TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_category    ON incidents(category);
CREATE INDEX IF NOT EXISTS idx_severity    ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_location    ON incidents(location);
CREATE INDEX IF NOT EXISTS idx_reported_at ON incidents(reported_at);
```

### 3.2 Field Definitions

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| id | INTEGER | auto | Primary key |
| title | TEXT | user input | Short summary of the incident |
| description | TEXT | user input | Full narrative, free text |
| location | TEXT | user input | Neighborhood / area string |
| reported_at | TEXT | auto | ISO 8601 timestamp when the event occurred (user can override) |
| created_at | TEXT | auto | ISO 8601 timestamp when the row was inserted |
| category | TEXT | classifier | One of: `cyber`, `criminal`, `infrastructure`, `suspicious`, `health`, `uncategorized` |
| severity | TEXT | classifier | One of: `critical`, `moderate`, `informational` |
| confidence | REAL | classifier | 0.0 for rule engine, 0.0–1.0 for AI |
| action | TEXT | classifier | Recommended action string |
| classified_by | TEXT | classifier | `ai` or `rule_engine` |
| matched_keywords | TEXT | classifier | JSON array string (rule engine) or NULL (AI) |
| fallback_reason | TEXT | classifier | NULL if AI succeeded, else error description |

### 3.3 Enum Constraints (enforced in application layer)

```python
VALID_CATEGORIES = {"cyber", "criminal", "infrastructure", "suspicious", "health", "uncategorized"}
VALID_SEVERITIES = {"critical", "moderate", "informational"}
VALID_CLASSIFIERS = {"ai", "rule_engine", "pending"}
```

---

## 4. Classifier Adapter

### 4.1 Public Interface

```python
def classify_report(title: str, description: str, reported_at: str) -> dict:
    """
    Classify an incident report. Tries AI first, falls back to rule engine.

    Returns:
        {
            "category": str,        # from VALID_CATEGORIES
            "severity": str,        # from VALID_SEVERITIES
            "confidence": float,    # 0.0 for rule engine, 0.0-1.0 for AI
            "action": str,          # recommended action text
            "classified_by": str,   # "ai" or "rule_engine"
            "matched_keywords": list|None,  # list of strings or None
            "fallback_reason": str|None     # None if AI succeeded
        }
    """
```

### 4.2 AI Path

**Provider:** Anthropic (Claude Sonnet — fast, cheap, sufficient for classification).
**Configuration:** `AI_PROVIDER` and `AI_API_KEY` from `.env`.

**System prompt:**

```
You are a community safety incident classifier. Given an incident report,
classify it and respond with ONLY a JSON object, no other text.

Categories (pick exactly one):
- cyber: digital threats, scams, phishing, data breaches, identity theft
- criminal: violent crime, theft, burglary, vandalism, assault
- infrastructure: power outages, road hazards, water issues, gas leaks, flooding
- suspicious: suspicious persons or vehicles, unusual activity, trespassing
- health: contamination, air quality, outbreaks, chemical spills
- uncategorized: does not fit any category

Severities (pick exactly one):
- critical: immediate danger, active threat, requires urgent action
- moderate: notable concern, should be aware, may require action
- informational: low urgency, awareness only, no action needed

Response format:
{
    "category": "<category>",
    "severity": "<severity>",
    "confidence": <float 0.0-1.0>,
    "action": "<1-2 sentence recommended action for affected residents>"
}
```

**User prompt:**

```
Title: {title}
Description: {description}
Reported at: {reported_at}
```

**Response handling:**

```python
def _try_ai_classification(title, description, reported_at):
    """
    Attempt AI classification. Returns classification dict or raises exception.
    """
    # 1. Build messages
    # 2. Call API with timeout=10s
    # 3. Extract response text
    # 4. Parse JSON (strip markdown fences if present)
    # 5. Validate: category in VALID_CATEGORIES, severity in VALID_SEVERITIES,
    #    confidence is float 0-1, action is non-empty string
    # 6. If validation fails, raise ValueError("ai_output_rejected")
    # 7. Return validated dict with classified_by="ai", matched_keywords=None,
    #    fallback_reason=None
```

**Caught exceptions → fallback:**

| Exception | fallback_reason |
|-----------|----------------|
| `requests.ConnectionError`, `requests.Timeout` | `"api_unreachable"` |
| `anthropic.AuthenticationError` | `"api_auth_error"` |
| `anthropic.RateLimitError` | `"api_rate_limited"` |
| `json.JSONDecodeError` | `"api_response_invalid"` |
| `ValueError` (validation) | `"ai_output_rejected"` |
| `Exception` (catch-all) | `"api_unknown_error"` |

### 4.3 Rule Engine Path

Loaded from `rules.json` at module import time. Pure function, zero I/O, zero dependencies.

**Stage 1 — Text normalization:**

```python
def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text
```

Input: concatenation of `title + " " + description`, normalized.

**Stage 2 — Category classification:**

Keyword dictionaries loaded from `rules.json`. Evaluated in priority order. A category matches if ANY primary keyword is found in the text, OR if 2+ secondary keywords are found.

Priority order: `cyber` → `criminal` → `infrastructure` → `suspicious` → `health` → `uncategorized`.

First match wins.

**Stage 3 — Severity scoring:**

Base severity by category:

| Category | Base |
|----------|------|
| cyber | critical |
| criminal | critical |
| infrastructure | moderate |
| suspicious | moderate |
| health | moderate |
| uncategorized | informational |

Modifier keywords (from `rules.json`):

- Escalators (+1): immediate, emergency, danger, active, ongoing, armed, right now, help, urgent, serious, spreading
- De-escalators (-1): possible, maybe, minor, old, resolved, yesterday, false alarm, might be, not sure, used to

Net modifier applied to base:
- Base critical + negative modifier → moderate
- Base moderate + positive modifier → critical
- Base moderate + negative modifier → informational
- Base informational + positive modifier → moderate
- Clamped to [informational, critical] range

Recency modifier:
- `reported_at` within 1 hour → +1
- `reported_at` older than 24 hours → -1
- Otherwise → 0

**Stage 4 — Action lookup:**

Flat dictionary in `rules.json` keyed by `"{category}_{severity}"`. Returns a static action string.

**Output construction:**

```python
{
    "category": matched_category,
    "severity": computed_severity,
    "confidence": 0.0,
    "action": ACTION_TABLE[f"{matched_category}_{computed_severity}"],
    "classified_by": "rule_engine",
    "matched_keywords": list_of_matched_keywords,
    "fallback_reason": reason_from_ai_failure  # or None if rule engine was called directly
}
```

### 4.4 Adapter Flow (pseudocode)

```python
def classify_report(title, description, reported_at):
    try:
        result = _try_ai_classification(title, description, reported_at)
        return result
    except Exception as e:
        reason = _map_exception_to_reason(e)
        result = _rule_engine_classify(title, description, reported_at)
        result["fallback_reason"] = reason
        return result
```

---

## 5. Rules Configuration File

### 5.1 `rules.json` Structure

```json
{
    "category_keywords": {
        "cyber": {
            "primary": ["phishing", "breach", "hack", "ransomware", "identity theft",
                        "scam", "fraud", "spoofing", "malware", "suspicious email",
                        "account compromised", "data leak", "password stolen"],
            "secondary": ["password", "link", "clicked", "bank", "credit card",
                          "verification code", "login", "email", "wire transfer",
                          "social security", "account", "unauthorized"]
        },
        "criminal": {
            "primary": ["assault", "robbery", "shooting", "weapon", "break in",
                        "burglary", "theft", "vandalism", "carjacking", "gunshot",
                        "stabbing", "mugging", "arson"],
            "secondary": ["police", "911", "witness", "suspect", "victim",
                          "arrested", "stolen", "smashed", "broken into", "threatening"]
        },
        "infrastructure": {
            "primary": ["power outage", "water main", "gas leak", "road closure",
                        "sinkhole", "flooding", "fire", "downed line", "construction",
                        "transformer", "sewage"],
            "secondary": ["utility", "blocked", "closed", "hazard", "damaged",
                          "repair crew", "detour", "outage", "leak", "broken"]
        },
        "suspicious": {
            "primary": ["suspicious person", "suspicious vehicle", "loitering",
                        "trespassing", "casing", "surveillance", "prowler",
                        "unfamiliar vehicle"],
            "secondary": ["camera", "ring", "noticed", "unusual", "repeated",
                          "doorbell", "watching", "parked", "circling", "stranger"]
        },
        "health": {
            "primary": ["contamination", "air quality", "chemical spill", "outbreak",
                        "boil water", "mold", "asbestos", "hazmat", "toxic"],
            "secondary": ["smell", "fumes", "sick", "advisory", "rash", "hospital",
                          "epa", "symptoms", "nausea", "headache"]
        }
    },
    "severity_escalators": ["immediate", "emergency", "danger", "active", "ongoing",
                            "armed", "right now", "help", "urgent", "serious", "spreading",
                            "multiple", "growing"],
    "severity_deescalators": ["possible", "maybe", "minor", "old", "resolved",
                              "yesterday", "false alarm", "might be", "not sure",
                              "used to", "no longer", "past"],
    "base_severity": {
        "cyber": "critical",
        "criminal": "critical",
        "infrastructure": "moderate",
        "suspicious": "moderate",
        "health": "moderate",
        "uncategorized": "informational"
    },
    "action_table": {
        "cyber_critical": "Change all passwords immediately. Enable two-factor authentication on all accounts. Check bank and credit card statements for unauthorized transactions. File a report with local cybercrime unit.",
        "cyber_moderate": "Review recent login activity on mentioned accounts. Update passwords for affected services. Monitor statements for the next 30 days.",
        "cyber_informational": "Be aware of this reported scam. Do not click unfamiliar links. No immediate action required.",
        "criminal_critical": "Call 911 immediately if not already done. Do not approach or intervene. Lock doors, stay inside, and await official all-clear.",
        "criminal_moderate": "Report to local police non-emergency line if not already reported. Secure your property. Review security camera footage if available.",
        "criminal_informational": "Incident logged for community awareness. No immediate action needed. Stay alert in the reported area.",
        "infrastructure_critical": "Evacuate the area if directed by authorities. Avoid the affected zone. Contact your utility provider's emergency line.",
        "infrastructure_moderate": "Avoid the affected area. Check your utility provider's status page for updates. Report to 311 if not already reported.",
        "infrastructure_informational": "Minor infrastructure issue noted. No immediate impact expected. Report to 311 if it worsens.",
        "suspicious_critical": "Do not approach. Call 911 if activity appears threatening. Lock doors and stay inside.",
        "suspicious_moderate": "Note details (vehicle plate, physical description, time). Report to police non-emergency line. Alert immediate neighbors.",
        "suspicious_informational": "Activity logged for community awareness. Stay alert but no action required.",
        "health_critical": "Follow official advisories immediately. Seal windows if air quality issue. Use bottled water if water advisory. Seek medical attention if symptomatic.",
        "health_moderate": "Monitor local health department updates. Limit exposure to affected area. Seek medical advice if you experience symptoms.",
        "health_informational": "Health advisory noted. No immediate risk reported. Monitor for updates.",
        "uncategorized_critical": "Review report details and assess personal risk. Contact local authorities if you believe you are affected.",
        "uncategorized_moderate": "Report logged for community review. No specific action recommended at this time.",
        "uncategorized_informational": "Report logged for community awareness. No action required."
    },
    "secondary_keyword_threshold": 2
}
```

---

## 6. Flask Application

### 6.1 Route Map

| Route | Method | Purpose | Template |
|-------|--------|---------|----------|
| `/` | GET | Redirect to `/feed` | — |
| `/feed` | GET | Incident feed with filters | `feed.html` |
| `/report/new` | GET | New report form | `report_form.html` |
| `/report/new` | POST | Submit new report | redirect to `/feed` |
| `/report/<id>` | GET | Report detail view | `report_detail.html` |
| `/report/<id>/edit` | GET | Edit report form | `report_form.html` (prefilled) |
| `/report/<id>/edit` | POST | Submit edited report | redirect to `/report/<id>` |
| `/digest` | GET | AI-generated summary of recent incidents | `digest.html` |

### 6.2 Route Details

#### `GET /feed`

**Query parameters:**
- `category` — filter by category (optional, from VALID_CATEGORIES)
- `severity` — filter by severity (optional, from VALID_SEVERITIES)
- `location` — filter by location substring (optional)
- `q` — search title and description (optional)
- `sort` — `newest` (default) or `oldest`

**SQL construction:**

```python
query = "SELECT * FROM incidents WHERE 1=1"
params = []

if category:
    query += " AND category = ?"
    params.append(category)
if severity:
    query += " AND severity = ?"
    params.append(severity)
if location:
    query += " AND location LIKE ?"
    params.append(f"%{location}%")
if q:
    query += " AND (title LIKE ? OR description LIKE ?)"
    params.extend([f"%{q}%", f"%{q}%"])

query += " ORDER BY reported_at DESC" if sort == "newest" else " ORDER BY reported_at ASC"
```

**Template renders:** List of incident cards, each showing title, category badge, severity badge, location, time, first 100 chars of description. Filter bar at top.

#### `POST /report/new`

**Input validation (before any processing):**

| Field | Rule | Error message |
|-------|------|---------------|
| title | Non-empty, 1–200 chars | "Title is required and must be under 200 characters." |
| description | Non-empty, 1–5000 chars | "Description is required and must be under 5000 characters." |
| location | Non-empty, 1–200 chars | "Location is required and must be under 200 characters." |

On validation failure: re-render form with error messages and preserved input values (Flask `flash()`).

On validation success:
1. Set `reported_at` = current ISO 8601 timestamp
2. Set `created_at` = current ISO 8601 timestamp
3. Call `classify_report(title, description, reported_at)`
4. Insert complete row into `incidents`
5. Flash success message: "Report submitted and classified as {category}/{severity}."
6. If fallback was used, additionally flash: "Note: AI classification was unavailable ({reason}). Report was classified using rule-based analysis."
7. Redirect to `/feed`

#### `POST /report/<id>/edit`

Same validation as create. On success:
1. Update user fields (title, description, location) in the row
2. Re-run `classify_report()` on the updated text
3. Update classification fields in the row
4. Redirect to `/report/<id>`

#### `GET /report/<id>`

Full detail view. All fields rendered. Classification metadata visible:
- Category and severity with color-coded badges
- Recommended action in a callout box
- "Classified by: AI (confidence: 0.87)" or "Classified by: Rule Engine (matched: phishing, bank, clicked)"
- If fallback: "AI was unavailable: api_timeout. Rule-based classification was used."

#### `GET /digest`

Optional/stretch feature. Calls the AI with the last N incidents to generate a narrative digest. If AI is unavailable, displays a simple statistical summary: "In the last 24 hours: 3 cyber incidents (2 critical), 1 criminal (moderate), 5 infrastructure (informational)." Rule-based digest is a SQL aggregation query, no AI needed.

### 6.3 Input Validation Module

```python
def validate_report_form(form_data: dict) -> tuple[dict, list[str]]:
    """
    Validate incoming report form data.

    Returns:
        (cleaned_data, errors)
        If errors is non-empty, cleaned_data should not be used.
    """
    errors = []
    cleaned = {}

    title = form_data.get("title", "").strip()
    if not title:
        errors.append("Title is required.")
    elif len(title) > 200:
        errors.append("Title must be under 200 characters.")
    cleaned["title"] = title

    description = form_data.get("description", "").strip()
    if not description:
        errors.append("Description is required.")
    elif len(description) > 5000:
        errors.append("Description must be under 5000 characters.")
    cleaned["description"] = description

    location = form_data.get("location", "").strip()
    if not location:
        errors.append("Location is required.")
    elif len(location) > 200:
        errors.append("Location must be under 200 characters.")
    cleaned["location"] = location

    return cleaned, errors
```

---

## 7. Frontend Templates

### 7.1 Template Hierarchy

```
templates/
├── base.html          ← shared layout, nav, flash messages
├── feed.html          ← incident list with filter bar
├── report_form.html   ← create/edit form (dual purpose)
├── report_detail.html ← single incident full view
└── digest.html        ← optional: AI digest or statistical summary
```

### 7.2 `base.html` — Shared Layout

- HTML5 boilerplate
- Minimal CSS (inline or single `static/style.css` — NO framework, keep it lean)
- Navigation: Feed | New Report | Digest
- Flash message rendering block
- Content block for child templates

### 7.3 `feed.html` — Incident Feed

**Filter bar (top of page):**
- Dropdown: Category (All / Cyber / Criminal / Infrastructure / Suspicious / Health / Uncategorized)
- Dropdown: Severity (All / Critical / Moderate / Informational)
- Text input: Location
- Text input: Search (title/description)
- Submit button: "Filter"
- Current active filters displayed as removable tags

**Incident cards (list):**
Each card shows:
- Title (linked to detail view)
- Category badge (color-coded: red=cyber/criminal, orange=infrastructure, yellow=suspicious, blue=health, gray=uncategorized)
- Severity badge (red=critical, orange=moderate, green=informational)
- Location
- Relative time ("2 hours ago")
- Description preview (first 100 characters, truncated with "...")
- Classified by indicator (small text: "AI" or "Rules")

**Empty state:** "No incidents match your filters." or "No incidents reported yet. Be the first to submit a report."

### 7.4 `report_form.html` — Create / Edit

Dual-purpose template. If `incident` is passed, pre-fills fields for editing.

Fields:
- Title: text input, `maxlength=200`, `required`
- Description: textarea, `maxlength=5000`, `required`, 6 rows
- Location: text input, `maxlength=200`, `required`
- Submit button: "Submit Report" (create) or "Update Report" (edit)

Validation errors displayed above form in a red callout.

### 7.5 `report_detail.html` — Detail View

Full incident display:
- Title (large)
- Category badge + Severity badge
- Location · Reported at (formatted) · Created at (formatted)
- Description (full text)
- Action callout box (styled differently from description — e.g., blue background)
- Classification metadata section:
  - Classified by: AI (confidence: X.XX) or Rule Engine
  - Matched keywords: [list] (if rule engine)
  - Fallback reason: {reason} (if applicable)
- Edit button → links to `/report/<id>/edit`

### 7.6 Color Scheme / Badge System

Minimal CSS. No framework. Intentionally plain — the rubric says "we don't score UI polish."

```css
/* Category badges */
.badge-cyber      { background: #dc3545; color: white; }
.badge-criminal   { background: #dc3545; color: white; }
.badge-infrastructure { background: #fd7e14; color: white; }
.badge-suspicious { background: #ffc107; color: black; }
.badge-health     { background: #0d6efd; color: white; }
.badge-uncategorized  { background: #6c757d; color: white; }

/* Severity badges */
.badge-critical      { background: #dc3545; color: white; }
.badge-moderate      { background: #fd7e14; color: white; }
.badge-informational { background: #198754; color: white; }

/* Action callout */
.action-box { background: #e7f1ff; border-left: 4px solid #0d6efd; padding: 12px; margin: 16px 0; }

/* Flash messages */
.flash-success { background: #d4edda; border: 1px solid #c3e6cb; padding: 10px; }
.flash-warning { background: #fff3cd; border: 1px solid #ffeeba; padding: 10px; }
.flash-error   { background: #f8d7da; border: 1px solid #f5c6cb; padding: 10px; }
```

---

## 8. Synthetic Seed Data

### 8.1 `sample_data.json`

15–20 incidents spanning all categories and severities. Mix of clear-cut and ambiguous cases (edge cases for rule engine testing). All locations reference a fictional neighborhood ("Maplewood Heights") with named sub-areas.

```json
[
    {
        "title": "Phishing email targeting residents",
        "description": "Multiple neighbors on Elm Street received an email claiming to be from the city water department asking them to verify their account by clicking a link. The email address was waterdept-billing@gmail.com which is not the official city address. At least three people clicked the link before it was identified as a scam.",
        "location": "Elm Street, Maplewood Heights",
        "reported_at": "2026-03-07T09:15:00Z"
    },
    {
        "title": "Car break-ins on Oak Avenue overnight",
        "description": "Three vehicles on Oak Avenue between 2nd and 4th had their windows smashed overnight. Dashcams, a laptop, and loose change were stolen. Police report filed, case #MH-2026-0412. One neighbor's Ring camera caught a figure in a dark hoodie at approximately 2:30 AM.",
        "location": "Oak Avenue, Maplewood Heights",
        "reported_at": "2026-03-07T07:30:00Z"
    },
    {
        "title": "Water main break on Cedar Lane",
        "description": "Major water main break at the intersection of Cedar Lane and 5th Street. Water is flooding the roadway. City utility crew has been called but no ETA yet. Road is impassable in both directions. Several basements on the south side are reporting water seepage.",
        "location": "Cedar Lane & 5th Street, Maplewood Heights",
        "reported_at": "2026-03-07T14:00:00Z"
    },
    {
        "title": "Unfamiliar van circling the block",
        "description": "A white unmarked van with no plates has been driving slowly through the Birchwood subdivision three times in the last two hours. It stops briefly in front of houses then moves on. Could be a delivery driver who is lost, but multiple neighbors have noticed and are concerned.",
        "location": "Birchwood Subdivision, Maplewood Heights",
        "reported_at": "2026-03-07T16:45:00Z"
    },
    {
        "title": "Strong gas smell near school",
        "description": "Residents near Maplewood Elementary are reporting a strong natural gas smell starting around noon. The smell is strongest near the playground area. No visible source identified. School is currently in session.",
        "location": "Maplewood Elementary, Pine Road",
        "reported_at": "2026-03-07T12:20:00Z"
    },
    {
        "title": "Ransomware attack on local dental office",
        "description": "Dr. Chen's dental office on Main Street was hit by ransomware. Patient records including names, insurance info, and SSNs may have been compromised. The office is notifying affected patients but if you were a patient there you should freeze your credit immediately.",
        "location": "Main Street, Maplewood Heights",
        "reported_at": "2026-03-07T10:00:00Z"
    },
    {
        "title": "Traffic light outage at major intersection",
        "description": "The traffic signal at Maple Drive and Highway 9 has been dark since this morning. Police have not yet set up traffic control. Multiple near-misses reported. Treat as a four-way stop.",
        "location": "Maple Drive & Highway 9, Maplewood Heights",
        "reported_at": "2026-03-07T08:45:00Z"
    },
    {
        "title": "Resolved: Missing dog found",
        "description": "Update on the golden retriever reported missing yesterday from Willow Park. The dog has been found safe and returned to its owner. Thank you to everyone who shared the post. No further action needed.",
        "location": "Willow Park, Maplewood Heights",
        "reported_at": "2026-03-06T15:00:00Z"
    },
    {
        "title": "Porch pirate active on Spruce Street",
        "description": "At least five packages have been stolen from porches on Spruce Street this week. Appears to be the same individual based on doorbell camera footage — male, mid-30s, gray sedan. Police have been notified. Consider requiring signatures for deliveries or using a package locker.",
        "location": "Spruce Street, Maplewood Heights",
        "reported_at": "2026-03-07T11:30:00Z"
    },
    {
        "title": "Possible coyote sighting near park",
        "description": "Maybe saw a coyote near the old baseball diamond at Riverside Park yesterday evening around dusk. Not 100% sure, might have been a large dog. Just wanted to mention it in case anyone with small pets wants to be extra careful.",
        "location": "Riverside Park, Maplewood Heights",
        "reported_at": "2026-03-06T18:30:00Z"
    },
    {
        "title": "Suspicious text messages from fake bank",
        "description": "Got a text saying my Chase account was locked and I needed to verify my identity through a link. I don't bank with Chase. Looks like a mass phishing campaign. The link goes to chase-secure-verify.com which is not a real Chase domain. Do not click.",
        "location": "Maplewood Heights (general)",
        "reported_at": "2026-03-07T13:10:00Z"
    },
    {
        "title": "Construction noise complaint",
        "description": "The new apartment complex on Ash Boulevard has been starting construction at 6 AM, which is before the city's allowed start time of 7 AM. This has been going on for two weeks. Minor inconvenience but might be worth a call to code enforcement.",
        "location": "Ash Boulevard, Maplewood Heights",
        "reported_at": "2026-03-05T06:30:00Z"
    },
    {
        "title": "Armed robbery at convenience store",
        "description": "Just heard there was an armed robbery at the QuickStop on 3rd and Maple about 20 minutes ago. Police are on scene. Suspect fled on foot heading east. Stay clear of the area. One employee was injured but is expected to be okay.",
        "location": "3rd & Maple Drive, Maplewood Heights",
        "reported_at": "2026-03-07T21:15:00Z"
    },
    {
        "title": "Boil water advisory for north side",
        "description": "City has issued a boil water advisory for all addresses north of Elm Street due to the water main break earlier today. Advisory is in effect until further notice. Boil tap water for at least one minute before drinking or cooking.",
        "location": "North Maplewood Heights",
        "reported_at": "2026-03-07T17:00:00Z"
    },
    {
        "title": "Kids skateboarding in the parking garage",
        "description": "Group of teenagers have been skateboarding in the Maplewood Plaza parking garage after hours again. Not dangerous exactly but they're making a lot of noise and the property manager has asked them to stop before. Low priority.",
        "location": "Maplewood Plaza, Maplewood Heights",
        "reported_at": "2026-03-07T20:00:00Z"
    }
]
```

### 8.2 Seed Script

```python
# seed.py
import json
from datetime import datetime, timezone
from db import get_db, init_db
from classifier import classify_report

def seed():
    init_db()
    with open("sample_data.json") as f:
        incidents = json.load(f)

    db = get_db()
    for inc in incidents:
        result = classify_report(inc["title"], inc["description"], inc["reported_at"])
        db.execute(
            """INSERT INTO incidents
               (title, description, location, reported_at, created_at,
                category, severity, confidence, action, classified_by,
                matched_keywords, fallback_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (inc["title"], inc["description"], inc["location"], inc["reported_at"],
             datetime.now(timezone.utc).isoformat(),
             result["category"], result["severity"], result["confidence"],
             result["action"], result["classified_by"],
             json.dumps(result["matched_keywords"]) if result["matched_keywords"] else None,
             result["fallback_reason"])
        )
    db.commit()
    print(f"Seeded {len(incidents)} incidents.")

if __name__ == "__main__":
    seed()
```

---

## 9. Test Plan

### 9.1 Test File: `test_classifier.py`

Framework: `pytest`

**Test 1 — Happy path (rule engine, clear category):**

```python
def test_rule_engine_classifies_phishing_as_cyber_critical():
    """Clear phishing report should be classified as cyber/critical."""
    result = classify_report(
        title="Phishing email targeting residents",
        description="Multiple neighbors received a phishing email asking them to click a suspicious link and enter their bank credentials.",
        reported_at=datetime.now(timezone.utc).isoformat()
    )
    assert result["category"] == "cyber"
    assert result["severity"] == "critical"
    assert result["classified_by"] in ("ai", "rule_engine")
    assert result["action"]  # non-empty
    assert result["category"] in VALID_CATEGORIES
    assert result["severity"] in VALID_SEVERITIES
```

**Test 2 — Edge case (empty/minimal description):**

```python
def test_rule_engine_handles_empty_description():
    """Report with minimal text should classify as uncategorized/informational."""
    result = classify_report(
        title="Something happened",
        description="Not sure what.",
        reported_at=datetime.now(timezone.utc).isoformat()
    )
    assert result["category"] == "uncategorized"
    assert result["severity"] == "informational"
    assert result["classified_by"] in ("ai", "rule_engine")
    assert result["action"]  # still provides an action
```

### 9.2 Optional Additional Tests (if time permits)

**Test 3 — Fallback behavior:**

```python
def test_fallback_on_invalid_api_key(monkeypatch):
    """With an invalid API key, should fall back to rule engine gracefully."""
    monkeypatch.setenv("AI_API_KEY", "invalid-key")
    result = classify_report(
        title="Armed robbery in progress",
        description="Active armed robbery at the corner store. Police called.",
        reported_at=datetime.now(timezone.utc).isoformat()
    )
    assert result["classified_by"] == "rule_engine"
    assert result["fallback_reason"] is not None
    assert result["category"] == "criminal"
```

**Test 4 — Severity de-escalation:**

```python
def test_severity_deescalation_on_resolved_incident():
    """Resolved/past incidents should have lower severity."""
    result = classify_report(
        title="Resolved: break-in from last week",
        description="The break-in on Oak Avenue has been resolved. Suspect was arrested yesterday. No further concern.",
        reported_at="2026-03-01T10:00:00Z"  # old
    )
    # criminal base is critical, but "resolved" + "yesterday" + old timestamp should deescalate
    assert result["severity"] in ("moderate", "informational")
```

**Test 5 — Input validation:**

```python
def test_validate_report_form_rejects_empty_title():
    cleaned, errors = validate_report_form({"title": "", "description": "test", "location": "test"})
    assert len(errors) > 0
    assert any("Title" in e for e in errors)
```

### 9.3 Running Tests

```bash
pytest test_classifier.py -v
```

---

## 10. Project Structure

```
community-guardian/
├── .env.example           ← committed, shows required vars without values
├── .gitignore             ← .env, *.db, __pycache__/, .pytest_cache/
├── requirements.txt       ← flask, anthropic, python-dotenv, pytest
├── README.md              ← filled-out template from assignment
├── sample_data.json       ← synthetic seed data (15 incidents)
├── rules.json             ← keyword dictionaries, action table
│
├── app.py                 ← Flask application, routes, template rendering
├── db.py                  ← SQLite init, get_db(), query helpers
├── classifier.py          ← classify_report(), AI path, rule engine, validation
├── seed.py                ← loads sample_data.json into SQLite via classifier
├── validators.py          ← validate_report_form()
│
├── templates/
│   ├── base.html
│   ├── feed.html
│   ├── report_form.html
│   ├── report_detail.html
│   └── digest.html
│
├── static/
│   └── style.css
│
└── tests/
    └── test_classifier.py
```

---

## 11. Environment & Configuration

### 11.1 `.env.example`

```
# AI Provider Configuration
AI_PROVIDER=anthropic
AI_API_KEY=your-api-key-here
AI_MODEL=claude-sonnet-4-20250514

# Flask Configuration
FLASK_SECRET_KEY=change-this-to-a-random-string
FLASK_DEBUG=false

# Database
DB_PATH=guardian.db
```

### 11.2 `.gitignore`

```
.env
*.db
__pycache__/
.pytest_cache/
*.pyc
.DS_Store
```

### 11.3 `requirements.txt`

```
flask>=3.0
anthropic>=0.42
python-dotenv>=1.0
pytest>=8.0
```

---

## 12. Success Metrics Alignment

| Rubric Criterion | How We Address It |
|-----------------|-------------------|
| **Anxiety Reduction** | AI (and fallback) generates specific, calm action items — not raw data dumps. Severity badges give instant visual triage. |
| **Contextual Relevance** | Location field enables per-neighborhood filtering. Category/severity classification provides immediate context. |
| **Trust & Privacy** | No user accounts or tracking. Location is self-reported free text, not geocoded. No PII collected beyond incident description. |
| **AI Application** | AI classifies incidents with structured output. Fallback is transparent, deterministic, and auditable. Adapter pattern makes the distinction invisible to the user. |
| **Problem Understanding** | Scenario framed as noise-to-signal problem — directly analogous to SOC alert fatigue. |
| **Technical Rigor** | Three-layer decoupled architecture. Input validation. Typed output schema enforced on both AI and rule engine. Exception handling with categorized fallback reasons. |
| **Creativity** | Severity scoring with escalator/de-escalator modifiers and recency weighting. Classification explainability via matched_keywords. |
| **Prototype Quality** | Full CRUD + search/filter. Seeded with 15 realistic incidents on first run. Tests pass. README is complete. |
| **Responsible AI** | AI output is validated before acceptance. Fallback is deterministic. User is informed which system classified their report. Confidence=0.0 for rule engine is honest. |
