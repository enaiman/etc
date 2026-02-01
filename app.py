import os
import re
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Jira issue type to emoji/label mapping
ISSUE_TYPE_MAP = {
    "bug": {"emoji": "\U0001f41b", "label": "Bug Fix"},
    "story": {"emoji": "\u2728", "label": "New Feature"},
    "task": {"emoji": "\u2705", "label": "Update"},
    "sub-task": {"emoji": "\u2705", "label": "Update"},
    "epic": {"emoji": "\U0001f4e6", "label": "New Initiative"},
    "improvement": {"emoji": "\U0001f680", "label": "Improvement"},
    "new feature": {"emoji": "\u2728", "label": "New Feature"},
    "change": {"emoji": "\U0001f504", "label": "Change"},
    "spike": {"emoji": "\U0001f50d", "label": "Research Update"},
}

DEFAULT_TYPE = {"emoji": "\U0001f4e3", "label": "Update"}


def parse_jira_url(url):
    """Extract Jira domain and issue key from a Jira URL."""
    # Matches: https://mycompany.atlassian.net/browse/PROJ-123
    #          https://mycompany.atlassian.net/jira/software/projects/PROJ/boards/1?selectedIssue=PROJ-123
    match = re.search(r"https?://([^/]+).*?([A-Z][A-Z0-9]+-\d+)", url)
    if not match:
        return None, None
    domain = match.group(1)
    issue_key = match.group(2)
    return domain, issue_key


def fetch_jira_issue(domain, issue_key, email, api_token):
    """Fetch issue data from Jira REST API."""
    url = f"https://{domain}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,issuetype,status,priority,description,labels,fixVersions,assignee,components"}
    response = requests.get(url, params=params, auth=(email, api_token), timeout=15)
    response.raise_for_status()
    return response.json()


def extract_text_from_adf(node):
    """Extract plain text from Atlassian Document Format (ADF) content."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    text = ""
    if node.get("type") == "text":
        text += node.get("text", "")
    for child in node.get("content", []):
        text += extract_text_from_adf(child)
    return text


def generate_slack_message(issue_data, jira_url):
    """Generate a friendly Slack announcement from Jira issue data."""
    fields = issue_data.get("fields", {})
    summary = fields.get("summary", "Untitled")
    issue_type_name = fields.get("issuetype", {}).get("name", "Task")
    status = fields.get("status", {}).get("name", "")
    description_adf = fields.get("description")
    fix_versions = fields.get("fixVersions", [])
    labels = fields.get("labels", [])

    # Get type mapping
    type_info = ISSUE_TYPE_MAP.get(issue_type_name.lower(), DEFAULT_TYPE)
    emoji = type_info["emoji"]
    label = type_info["label"]

    # Extract a brief description (first ~200 chars of plain text)
    description_text = extract_text_from_adf(description_adf).strip() if description_adf else ""
    brief_desc = ""
    if description_text:
        # Take first sentence or first 200 chars
        first_sentence = re.split(r"(?<=[.!?])\s", description_text)[0]
        if len(first_sentence) > 200:
            brief_desc = first_sentence[:197].rsplit(" ", 1)[0] + "..."
        else:
            brief_desc = first_sentence

    # Build the message
    lines = []
    lines.append(f"{emoji} *{label}: {summary}*")
    lines.append("")

    if brief_desc:
        lines.append(brief_desc)
        lines.append("")

    if status:
        status_emoji = "\U0001f7e2" if status.lower() in ("done", "closed", "resolved", "released") else "\U0001f539"
        lines.append(f"{status_emoji} *Status:* {status}")

    if fix_versions:
        version_names = ", ".join(v.get("name", "") for v in fix_versions if v.get("name"))
        if version_names:
            lines.append(f"\U0001f3f7\ufe0f *Version:* {version_names}")

    lines.append("")
    lines.append(f"\U0001f517 <{jira_url}|View in Jira>")

    return "\n".join(lines)


@app.route("/")
def index():
    return render_template(
        "index.html",
        default_email=os.getenv("JIRA_EMAIL", ""),
        default_token=os.getenv("JIRA_API_TOKEN", ""),
        default_domain=os.getenv("JIRA_DOMAIN", ""),
    )


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json()
    jira_url = data.get("jira_url", "").strip()
    email = data.get("email", "").strip() or os.getenv("JIRA_EMAIL", "")
    api_token = data.get("api_token", "").strip() or os.getenv("JIRA_API_TOKEN", "")

    if not jira_url:
        return jsonify({"error": "Please provide a Jira ticket URL."}), 400

    domain, issue_key = parse_jira_url(jira_url)
    if not domain or not issue_key:
        return jsonify({"error": "Could not parse the Jira URL. Expected format: https://yoursite.atlassian.net/browse/PROJ-123"}), 400

    if not email or not api_token:
        return jsonify({"error": "Jira credentials are missing. Provide them in the settings or set JIRA_EMAIL and JIRA_API_TOKEN environment variables."}), 400

    try:
        issue_data = fetch_jira_issue(domain, issue_key, email, api_token)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            return jsonify({"error": "Authentication failed. Check your email and API token."}), 401
        if e.response.status_code == 404:
            return jsonify({"error": f"Issue {issue_key} not found. Check the URL and your permissions."}), 404
        return jsonify({"error": f"Jira API error: {e.response.status_code}"}), 502
    except requests.exceptions.ConnectionError:
        return jsonify({"error": f"Could not connect to {domain}. Check the URL and your network."}), 502
    except requests.exceptions.Timeout:
        return jsonify({"error": "Request to Jira timed out. Try again."}), 504

    message = generate_slack_message(issue_data, jira_url)

    return jsonify({
        "message": message,
        "issue_key": issue_key,
        "summary": issue_data.get("fields", {}).get("summary", ""),
        "issue_type": issue_data.get("fields", {}).get("issuetype", {}).get("name", ""),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
