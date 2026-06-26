# Dreamsland Realty CRM — Interview Showcase Notes

## What was improved
- Added a dedicated **HubSpot CRM Workspace** at `/crm-workspace/`.
- Added **dark/light theme toggle** with localStorage persistence.
- Added CRM-oriented modules: lead scoring, pipeline stages, automation playbooks, HubSpot object mapping, and interview proof points.
- Fixed a major runtime issue: Firestore credentials no longer crash Django during import. The app now runs in demo-safe mode if credentials are unavailable.
- Converted `requirements.txt` from UTF-16 to normal UTF-8 text so pip can read it reliably.
- Added `.env.example` so configuration is clean and interview-friendly.

## How to present this in the interview
Say:
> “I converted the real estate admin panel into a CRM-style workspace inspired by HubSpot. It tracks contacts, properties, agents, lead stages, lead scoring, workflow automation, and dashboard reporting. I also added a dark/light theme toggle and made the project safer for local demos by handling Firebase gracefully.”

## HubSpot CRM specialist points to mention
- Contact lifecycle stages: New, Contacted, Qualified, Site Visit, Negotiation, Closed Won.
- Lead scoring based on phone, location, message intent, and sales stage.
- Workflow automation playbooks for speed-to-lead, property matching, and stale deal follow-up.
- Custom CRM objects: Contacts, Deals, Properties, Activities.
- Dashboard reporting for pipeline health, properties, agents, and active customers.

## Setup
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python manage.py runserver
```

For Firebase-backed data, set `FIREBASE_CREDENTIALS_PATH` to your private service account JSON path. Do not commit service account keys.
