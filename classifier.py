"""
Classifier adapter for Community Guardian.
Tries AI classification first, falls back to deterministic rule engine.
Pure function: text in, classification dict out. Knows nothing about HTTP or database.
"""

import json
import os
import re
import logging
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --- Constants ---

VALID_CATEGORIES = {"cyber", "criminal", "infrastructure", "suspicious", "health", "uncategorized"}
VALID_SEVERITIES = {"critical", "moderate", "informational"}

SEVERITY_ORDER = ["informational", "moderate", "critical"]

# --- Load rules.json at import time ---

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.json")
with open(_RULES_PATH) as _f:
    RULES = json.load(_f)

# --- Public Interface ---


def classify_report(title: str, description: str, reported_at: str) -> dict:
    """
    Classify an incident report. Tries AI first, falls back to rule engine.

    Args:
        title: Short incident title
        description: Full incident description
        reported_at: ISO 8601 timestamp

    Returns:
        dict with keys: category, severity, confidence, action,
                        classified_by, matched_keywords, fallback_reason
    """
    api_key = os.getenv("AI_API_KEY", "")
    ai_enabled = bool(api_key and api_key != "your-api-key-here")

    if ai_enabled:
        try:
            result = _try_ai_classification(title, description, reported_at, api_key)
            return result
        except anthropic.AuthenticationError:
            reason = "api_auth_error"
        except anthropic.RateLimitError:
            reason = "api_rate_limited"
        except anthropic.APITimeoutError:
            reason = "api_unreachable"
        except anthropic.APIConnectionError:
            reason = "api_unreachable"
        except json.JSONDecodeError:
            reason = "api_response_invalid"
        except ValueError as e:
            reason = str(e) if str(e) else "ai_output_rejected"
        except Exception as e:
            logger.warning(f"Unexpected AI error: {type(e).__name__}: {e}")
            reason = "api_unknown_error"

        logger.info(f"AI classification failed ({reason}), falling back to rule engine")
    else:
        reason = None  # rule engine used by choice, not failure

    result = _rule_engine_classify(title, description, reported_at)
    if reason:
        result["fallback_reason"] = reason
    return result


def classify_report_rule_engine_only(title: str, description: str, reported_at: str) -> dict:
    """Force rule engine classification (for testing)."""
    return _rule_engine_classify(title, description, reported_at)


# --- AI Classification ---

SYSTEM_PROMPT = """You are a community safety incident classifier. Given an incident report, classify it and respond with ONLY a JSON object, no other text, no markdown fences.

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

Response format (JSON only, no other text):
{"category": "<category>", "severity": "<severity>", "confidence": <float 0.0-1.0>, "action": "<1-2 sentence recommended action for affected residents>"}"""


def _try_ai_classification(title: str, description: str, reported_at: str, api_key: str) -> dict:
    """
    Attempt AI classification via Anthropic API.
    Returns validated classification dict.
    Raises on any failure (caught by caller).
    """
    model = os.getenv("AI_MODEL", "claude-sonnet-4-20250514")

    client = anthropic.Anthropic(api_key=api_key, timeout=15.0)

    user_message = f"Title: {title}\nDescription: {description}\nReported at: {reported_at}"

    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

    parsed = json.loads(raw_text)

    # Validate
    if parsed.get("category") not in VALID_CATEGORIES:
        raise ValueError("ai_output_rejected: invalid category")
    if parsed.get("severity") not in VALID_SEVERITIES:
        raise ValueError("ai_output_rejected: invalid severity")

    conf = parsed.get("confidence", 0.0)
    if not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
        conf = 0.5  # salvage with default rather than rejecting

    action = parsed.get("action", "")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("ai_output_rejected: empty action")

    return {
        "category": parsed["category"],
        "severity": parsed["severity"],
        "confidence": round(float(conf), 2),
        "action": action.strip(),
        "classified_by": "ai",
        "matched_keywords": None,
        "fallback_reason": None,
    }


# --- Rule Engine Classification ---


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _rule_engine_classify(title: str, description: str, reported_at: str) -> dict:
    """
    Deterministic keyword-based classification.
    Pure function, zero I/O, zero dependencies beyond rules loaded at import.
    """
    full_text = _normalize(title + " " + description)

    # Stage 1: Category classification
    category, matched = _match_category(full_text)

    # Stage 2: Severity scoring
    severity = _compute_severity(full_text, category, reported_at)

    # Stage 3: Action lookup
    action_key = f"{category}_{severity}"
    action = RULES["action_table"].get(action_key, "Report logged. No specific action recommended.")

    return {
        "category": category,
        "severity": severity,
        "confidence": 0.0,
        "action": action,
        "classified_by": "rule_engine",
        "matched_keywords": matched,
        "fallback_reason": None,
    }


def _match_category(text: str) -> tuple[str, list[str]]:
    """
    Match text against keyword dictionaries in priority order.
    Returns (category, list_of_matched_keywords).
    """
    threshold = RULES.get("secondary_keyword_threshold", 2)
    priority_order = ["cyber", "criminal", "infrastructure", "suspicious", "health"]

    for cat in priority_order:
        keywords = RULES["category_keywords"].get(cat, {})
        primary = keywords.get("primary", [])
        secondary = keywords.get("secondary", [])

        matched_primary = [kw for kw in primary if kw in text]
        matched_secondary = [kw for kw in secondary if kw in text]

        if matched_primary:
            return cat, matched_primary + matched_secondary
        elif len(matched_secondary) >= threshold:
            return cat, matched_secondary

    return "uncategorized", []


def _compute_severity(text: str, category: str, reported_at: str) -> str:
    """
    Compute severity from base + modifiers + recency.
    """
    base = RULES["base_severity"].get(category, "informational")
    base_idx = SEVERITY_ORDER.index(base)

    # Keyword modifiers
    escalators = RULES.get("severity_escalators", [])
    deescalators = RULES.get("severity_deescalators", [])

    modifier = 0
    for kw in escalators:
        if kw in text:
            modifier += 1
    for kw in deescalators:
        if kw in text:
            modifier -= 1

    # Recency modifier
    try:
        report_time = datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours_ago = (now - report_time).total_seconds() / 3600
        if hours_ago <= 1:
            modifier += 1
        elif hours_ago > 24:
            modifier -= 1
    except (ValueError, TypeError):
        pass  # unparseable timestamp, skip recency modifier

    # Apply modifier, clamped to valid range
    final_idx = max(0, min(len(SEVERITY_ORDER) - 1, base_idx + modifier))
    return SEVERITY_ORDER[final_idx]
