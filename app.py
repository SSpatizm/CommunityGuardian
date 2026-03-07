"""
Flask application for Community Guardian.
Handles HTTP, forms, template rendering. No business logic.
"""

import os
import json
from datetime import datetime, timezone

from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

from db import init_db, insert_incident, update_incident, get_incident, query_incidents, get_incident_stats, now_iso
from classifier import classify_report, VALID_CATEGORIES, VALID_SEVERITIES
from validators import validate_report_form

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-in-production")


# --- Initialize DB on startup ---
with app.app_context():
    init_db()


# --- Template Helpers ---

@app.template_filter("timeago")
def timeago_filter(iso_str):
    """Convert ISO timestamp to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = now - dt

        seconds = int(diff.total_seconds())
        if seconds < 0:
            return "just now"
        elif seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = seconds // 3600
            return f"{hours}h ago"
        elif seconds < 604800:
            days = seconds // 86400
            return f"{days}d ago"
        else:
            return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return iso_str


@app.template_filter("formatdate")
def formatdate_filter(iso_str):
    """Format ISO timestamp for display."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%B %d, %Y at %I:%M %p UTC")
    except (ValueError, TypeError):
        return iso_str


# --- Routes ---

@app.route("/")
def index():
    """Redirect to feed."""
    return redirect(url_for("feed"))


@app.route("/feed")
def feed():
    """Incident feed with filters."""
    category = request.args.get("category", "").strip()
    severity = request.args.get("severity", "").strip()
    location = request.args.get("location", "").strip()
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest").strip()

    # Validate filter values
    if category and category not in VALID_CATEGORIES:
        category = ""
    if severity and severity not in VALID_SEVERITIES:
        severity = ""

    incidents = query_incidents(
        category=category or None,
        severity=severity or None,
        location=location or None,
        q=q or None,
        sort=sort,
    )

    active_filters = {}
    if category:
        active_filters["category"] = category
    if severity:
        active_filters["severity"] = severity
    if location:
        active_filters["location"] = location
    if q:
        active_filters["q"] = q

    stats = get_incident_stats()

    return render_template(
        "feed.html",
        incidents=incidents,
        categories=sorted(VALID_CATEGORIES),
        severities=["critical", "moderate", "informational"],
        active_filters=active_filters,
        current_sort=sort,
        total=len(incidents),
        stats=stats,
    )


@app.route("/report/new", methods=["GET"])
def new_report_form():
    """Show the new report form."""
    return render_template("report_form.html", incident=None, editing=False)


@app.route("/report/new", methods=["POST"])
def create_report():
    """Validate, classify, and store a new incident report."""
    cleaned, errors = validate_report_form(request.form)

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("report_form.html", incident=request.form, editing=False), 400

    reported_at = now_iso()
    created_at = now_iso()

    # Classify
    result = classify_report(cleaned["title"], cleaned["description"], reported_at)

    # Build row data
    data = {
        "title": cleaned["title"],
        "description": cleaned["description"],
        "location": cleaned["location"],
        "reported_at": reported_at,
        "created_at": created_at,
        **result,
    }

    row_id = insert_incident(data)

    flash(f"Report submitted — classified as {result['category']}/{result['severity']}.", "success")
    if result.get("fallback_reason"):
        flash(
            f"Note: AI was unavailable ({result['fallback_reason']}). Rule-based classification was used.",
            "warning",
        )

    return redirect(url_for("report_detail", incident_id=row_id))


@app.route("/report/<int:incident_id>")
def report_detail(incident_id):
    """Show full detail for a single incident."""
    incident = get_incident(incident_id)
    if incident is None:
        flash("Incident not found.", "error")
        return redirect(url_for("feed"))
    return render_template("report_detail.html", incident=incident)


@app.route("/report/<int:incident_id>/edit", methods=["GET"])
def edit_report_form(incident_id):
    """Show the edit form for an existing report."""
    incident = get_incident(incident_id)
    if incident is None:
        flash("Incident not found.", "error")
        return redirect(url_for("feed"))
    return render_template("report_form.html", incident=incident, editing=True)


@app.route("/report/<int:incident_id>/edit", methods=["POST"])
def update_report(incident_id):
    """Validate, reclassify, and update an existing incident."""
    incident = get_incident(incident_id)
    if incident is None:
        flash("Incident not found.", "error")
        return redirect(url_for("feed"))

    cleaned, errors = validate_report_form(request.form)

    if errors:
        for e in errors:
            flash(e, "error")
        merged = {**incident, **dict(request.form)}
        return render_template("report_form.html", incident=merged, editing=True), 400

    # Reclassify with updated text
    result = classify_report(cleaned["title"], cleaned["description"], incident["reported_at"])

    data = {
        "title": cleaned["title"],
        "description": cleaned["description"],
        "location": cleaned["location"],
        **result,
    }

    update_incident(incident_id, data)

    flash("Report updated and reclassified.", "success")
    if result.get("fallback_reason"):
        flash(
            f"Note: AI was unavailable ({result['fallback_reason']}). Rule-based classification was used.",
            "warning",
        )

    return redirect(url_for("report_detail", incident_id=incident_id))


@app.route("/digest")
def digest():
    """Community safety digest — statistical summary of all incidents."""
    stats = get_incident_stats()
    recent = query_incidents(sort="newest")[:10]
    return render_template("digest.html", stats=stats, recent=recent)


# --- Error Handlers ---

@app.errorhandler(404)
def not_found(e):
    flash("Page not found.", "error")
    return redirect(url_for("feed"))


@app.errorhandler(500)
def server_error(e):
    flash("An unexpected error occurred. Please try again.", "error")
    return redirect(url_for("feed"))


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=8888, debug=debug)
