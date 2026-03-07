"""
Form validation for Community Guardian.
Pure functions: form data in, (cleaned_data, errors) out.
"""


def validate_report_form(form_data: dict) -> tuple[dict, list[str]]:
    """
    Validate incoming report form data.

    Args:
        form_data: dict with keys title, description, location

    Returns:
        (cleaned_data, errors) — if errors is non-empty, cleaned_data should not be used.
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
