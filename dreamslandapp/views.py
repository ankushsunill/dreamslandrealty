import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime

import firebase_admin
import requests
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from firebase_admin import credentials, firestore, storage


API_BASE_URL = django_settings.DREAMSLAND_API_BASE_URL.rstrip("/")
LEAD_PIPELINE_STAGES = [
    "New",
    "Contacted",
    "Qualified",
    "Site Visit",
    "Negotiation",
    "Closed Won",
]


def initialize_firestore():
    """Initialise Firestore when credentials are available.

    The previous version raised during module import if Firebase credentials were
    missing/invalid. That makes demos, local reviews, and interview walk-throughs
    fail before Django can even render a page. We now degrade gracefully: pages
    still load with empty/sample metrics and the issue is logged in the console.
    """
    credential_path = django_settings.FIREBASE_CREDENTIALS_PATH
    if not os.path.exists(credential_path):
        print("Firebase credentials not found. Running in demo-safe mode.")
        return None

    try:
        if not firebase_admin._apps:
            options = {}
            if django_settings.FIREBASE_STORAGE_BUCKET:
                options["storageBucket"] = django_settings.FIREBASE_STORAGE_BUCKET
            firebase_admin.initialize_app(
                credentials.Certificate(credential_path),
                options or None,
            )
        return firestore.client()
    except Exception as exc:
        print(f"Firebase initialisation failed. Running in demo-safe mode: {exc}")
        return None


db = initialize_firestore()


def api_get(path, params=None, default=None, timeout=8):
    try:
        response = requests.get(f"{API_BASE_URL}{path}", params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return default


def fetch_collection(collection_name, limit=None, order_by=None, direction=firestore.Query.DESCENDING):
    if db is None:
        return []
    try:
        query = db.collection(collection_name)
        if order_by:
            query = query.order_by(order_by, direction=direction)
        if limit:
            query = query.limit(limit)

        records = []
        for doc in query.stream():
            data = doc.to_dict()
            data["id"] = doc.id
            records.append(data)
        return records
    except Exception as exc:
        print(f"Firestore fetch failed for {collection_name}: {exc}")
        return []


def clean_payload(payload):
    return {key: value for key, value in payload.items() if value is not None}


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()

    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def normalize_lead_status(status):
    value = str(status or "New").strip().lower()
    aliases = {
        "pending": "New",
        "new": "New",
        "open": "New",
        "contacted": "Contacted",
        "called": "Contacted",
        "qualified": "Qualified",
        "hot": "Qualified",
        "site visit": "Site Visit",
        "visit": "Site Visit",
        "negotiation": "Negotiation",
        "proposal": "Negotiation",
        "converted": "Closed Won",
        "closed won": "Closed Won",
        "won": "Closed Won",
        "lost": "Closed Lost",
        "closed lost": "Closed Lost",
    }
    return aliases.get(value, status or "New")


def lead_score(lead):
    score = 30
    if lead.get("phone") or lead.get("Phone") or lead.get("contact"):
        score += 20
    if lead.get("location") or lead.get("Location"):
        score += 15
    if lead.get("message") or lead.get("Message"):
        score += 10
    if normalize_lead_status(lead.get("status")) in {"Qualified", "Site Visit", "Negotiation"}:
        score += 20
    return min(score, 100)


def save_uploaded_images(files, folder="property_images"):
    image_urls = []
    for image in files:
        safe_name = os.path.basename(image.name)
        path = default_storage.save(f"{folder}/{safe_name}", ContentFile(image.read()))
        image_urls.append(default_storage.url(path))
    return image_urls


def firebase_bucket():
    if django_settings.FIREBASE_STORAGE_BUCKET:
        return storage.bucket(django_settings.FIREBASE_STORAGE_BUCKET)
    return storage.bucket()


def build_pipeline(leads):
    status_counts = Counter(normalize_lead_status(lead.get("status")) for lead in leads)
    return [
        {
            "name": stage,
            "count": status_counts.get(stage, 0),
            "conversion": min(100, 18 + (index * 12) + status_counts.get(stage, 0) * 2),
        }
        for index, stage in enumerate(LEAD_PIPELINE_STAGES)
    ]


def loginpage(request):
    return render(request, "loginpage.html")


def login(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        # 1) Local/Django admin login: works with users created by createsuperuser.
        user = authenticate(request, username=username, password=password)
        if user is not None and user.is_staff:
            auth_login(request, user)
            request.session["admin_logged_in"] = True
            request.session["admin_username"] = username
            return redirect("admindashboard")

        # 2) Demo fallback: useful when Firebase credentials are not available locally.
        demo_username = os.environ.get("DREAMSLAND_DEMO_ADMIN_USERNAME", "admin")
        demo_password = os.environ.get("DREAMSLAND_DEMO_ADMIN_PASSWORD", "admin123")
        if username == demo_username and password == demo_password:
            request.session["admin_logged_in"] = True
            request.session["admin_username"] = username
            return redirect("admindashboard")

        # 3) Production Firebase admin collection login, only when Firestore is configured.
        if db is not None:
            try:
                admins = db.collection("admin").stream()
                for admin in admins:
                    admin_data = admin.to_dict()
                    if admin_data.get("Username") == username and admin_data.get("Password") == password:
                        request.session["admin_logged_in"] = True
                        request.session["admin_username"] = username
                        return redirect("admindashboard")
            except Exception as exc:
                print(f"Admin Firestore login failed: {exc}")

        messages.error(request, "Invalid username or password")
        return redirect("loginpage")

    return render(request, "loginpage.html")


def logout(request):
    request.session.flush()
    return redirect("loginpage")


def admindashboard(request):
    now = datetime.now()
    params = {"groupBy": "month", "year": now.year, "month": now.month}
    month_data = api_get("/api/analytics", params=params, default={}) or {}
    week_ranges = [entry.get("weekRange", "") for entry in month_data.get("data", [])]
    week_counts = [entry.get("count", 0) for entry in month_data.get("data", [])]

    properties_data = api_get("/getProperties", default=None)
    if not isinstance(properties_data, list):
        properties_data = fetch_collection("properties")

    agents = fetch_collection("agents")
    leads = fetch_collection("leads", limit=250)
    users = fetch_collection("users", limit=250)
    contact_requests = fetch_collection("contact_requests", limit=250)
    activity_logs, _ = get_activity_logs(limit=5)
    pipeline = build_pipeline(leads)

    assigned_properties = sum(
        1
        for prop in properties_data
        if prop.get("agent") or prop.get("AgentId") or prop.get("agentId")
    )
    customer_keys = {
        item
        for item in [
            *(lead.get("phone") or lead.get("email") or lead.get("id") for lead in leads),
            *(user.get("phone") or user.get("email") or user.get("id") for user in users),
            *(req.get("phone") or req.get("name") or req.get("id") for req in contact_requests),
        ]
        if item
    }
    lead_status_counts = Counter(normalize_lead_status(lead.get("status")) for lead in leads)

    context = {
        "month": now.strftime("%B"),
        "total_count": month_data.get("totalCount", 0),
        "week_ranges": week_ranges or ["Week 1", "Week 2", "Week 3", "Week 4"],
        "week_counts": week_counts or [0, 0, 0, 0],
        "total_properties": len(properties_data),
        "total_agents": len(agents),
        "assigned_properties": assigned_properties,
        "active_customers": len(customer_keys) or len(leads) + len(contact_requests),
        "lead_status": [lead_status_counts.get(stage, 0) for stage in LEAD_PIPELINE_STAGES],
        "pipeline_stages": pipeline,
        "recent_activity": activity_logs,
        "automation_playbooks": [
            {"name": "Lead capture to CRM", "status": "Live", "coverage": "Forms, calls, WhatsApp"},
            {"name": "Auto assignment", "status": "Ready", "coverage": "Location and budget routing"},
            {"name": "Site visit nurture", "status": "Optimized", "coverage": "Reminders and follow-up SLAs"},
        ],
        "hubspot_readiness": [
            "Contact lifecycle stages mapped",
            "Deal pipeline mirrors real estate sales journey",
            "Property custom object ready for CRM sync",
            "Lead score uses intent, location, and engagement signals",
        ],
    }
    return render(request, "admindashboard.html", context)


def analytics(request):
    current_year = datetime.now().year
    try:
        year = int(request.GET.get("year", current_year))
    except (TypeError, ValueError):
        year = current_year

    all_months_data = []
    weekly_totals = []

    for month in range(1, 13):
        params = {"groupBy": "month", "year": year, "month": month}
        month_data = api_get("/api/analytics", params=params, default={"totalCount": 0, "data": []}) or {
            "totalCount": 0,
            "data": [],
        }
        weekly_totals.extend(entry.get("count", 0) for entry in month_data.get("data", []))
        all_months_data.append(
            {
                "month": month,
                "month_name": datetime(year, month, 1).strftime("%B"),
                "data": month_data,
            }
        )

    total_properties = sum(item["data"].get("totalCount", 0) for item in all_months_data)
    peak_month = max(all_months_data, key=lambda item: item["data"].get("totalCount", 0), default=None)
    first_quarter = sum(item["data"].get("totalCount", 0) for item in all_months_data[:3])
    current_quarter = sum(item["data"].get("totalCount", 0) for item in all_months_data[-3:])
    growth_rate = ((current_quarter - first_quarter) / first_quarter * 100) if first_quarter else 0

    available_years = sorted({year, current_year, current_year - 1, current_year - 2, current_year + 1})
    return render(
        request,
        "analytics.html",
        {
            "year": year,
            "available_years": available_years,
            "analytics": all_months_data,
            "total_properties": total_properties,
            "peak_month": {
                "month_name": peak_month["month_name"] if peak_month else "N/A",
                "count": peak_month["data"].get("totalCount", 0) if peak_month else 0,
            },
            "avg_weekly": sum(weekly_totals) / len(weekly_totals) if weekly_totals else 0,
            "growth_rate": growth_rate,
        },
    )


def format_price_inr(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if value >= 1_00_00_000:
        return f"Rs. {value / 1_00_00_000:.2f} Cr"
    if value >= 1_00_000:
        return f"Rs. {value / 1_00_000:.2f} Lakh"
    if value >= 1_000:
        return f"Rs. {value / 1_000:.2f} Thousand"
    return f"Rs. {int(value)}"


def properties(request):
    try:
        docs = db.collection("properties").stream()
        properties_data = []

        for doc in docs:
            data = doc.to_dict()
            data["propertyId"] = doc.id
            data["formatted_price"] = format_price_inr(data.get("price"))

            if not data.get("location"):
                city = data.get("city", "")
                state = data.get("state", "")
                pincode = data.get("pincode", "")
                data["location"] = f"{city}, {state} - {pincode}".strip(" , -")

            data["json_script_id"] = f"property-data-{doc.id}"
            data["safe_json"] = json_safe(data)
            properties_data.append(data)
    except Exception as exc:
        print("Firestore Error:", exc)
        properties_data = []

    return render(request, "properties.html", {"properties": properties_data})


@csrf_exempt
def update_property(request):
    if request.method == "POST":
        property_id = request.POST.get("property_id")
        if not property_id:
            messages.error(request, "Property ID is required.")
            return redirect("properties")

        updated_data = clean_payload(
            {
                "name": request.POST.get("name"),
                "location": request.POST.get("location"),
                "type": request.POST.get("type"),
                "subtype": request.POST.get("subtype"),
                "bhk": request.POST.get("bhk"),
                "sqft": request.POST.get("sqft"),
                "price": request.POST.get("price"),
                "plot_area": request.POST.get("plot_area"),
                "unit": request.POST.get("unit"),
                "listed_on": request.POST.get("listed_on"),
                "owner_name": request.POST.get("owner_name"),
                "phone_number": request.POST.get("phone_number"),
                "whatsapp_number": request.POST.get("whatsapp_number"),
                "agent": request.POST.get("agent"),
                "status": request.POST.get("status"),
                "pricing_options": request.POST.get("pricing_options"),
                "remarks": request.POST.get("remarks"),
                "property_description": request.POST.get("property_description"),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            }
        )

        try:
            db.collection("properties").document(property_id).update(updated_data)
            messages.success(request, "Property updated successfully.")
        except Exception as exc:
            messages.error(request, f"Update failed: {exc}")

    return redirect("properties")


@csrf_exempt
def delete_property(request):
    if request.method == "POST":
        property_id = request.POST.get("property_id")
        try:
            db.collection("properties").document(property_id).delete()
            messages.success(request, "Property deleted successfully.")
        except Exception as exc:
            messages.error(request, f"Delete failed: {exc}")

    return redirect("properties")


def settings(request):
    return render(request, "settings.html")


def reports(request):
    return render(request, "reports.html")


def agents(request):
    agents_data = fetch_collection("agents")

    def extract_agent_number(agent):
        match = re.search(r"\d+", agent.get("AgentId", ""))
        return int(match.group()) if match else float("inf")

    agents_data.sort(key=extract_agent_number)
    return render(request, "agents.html", {"agents": agents_data})


def search_properties(request):
    query = request.GET.get("query", "").lower()
    results = []

    if query:
        try:
            for doc in db.collection("properties").stream():
                data = doc.to_dict()
                data["PropertyId"] = doc.id
                searchable_fields = ["PropertyId", "propertyId", "AgentId", "agent", "Location", "location", "Name", "name"]
                if any(query in str(data.get(field, "")).lower() for field in searchable_fields):
                    results.append(
                        {
                            "type": "property",
                            "PropertyId": data.get("PropertyId", ""),
                            "AgentId": data.get("AgentId", data.get("agent", "")),
                            "Location": data.get("Location", data.get("location", "")),
                            "Name": data.get("Name", data.get("name", "")),
                        }
                    )

            for doc in db.collection("agents").stream():
                data = doc.to_dict()
                data["AgentId"] = data.get("AgentId", "")
                if query in str(data.get("AgentId", "")).lower() or query in str(data.get("Name", "")).lower():
                    results.append(
                        {
                            "type": "agent",
                            "PropertyId": "-",
                            "AgentId": data.get("AgentId", ""),
                            "Location": data.get("Location", "-"),
                            "Name": data.get("Name", ""),
                        }
                    )

            for doc in db.collection("property_location").stream():
                data = doc.to_dict()
                if query in str(data.get("location", "")).lower():
                    results.append(
                        {
                            "type": "location",
                            "PropertyId": "-",
                            "AgentId": "-",
                            "Location": data.get("location", ""),
                            "Name": "-",
                        }
                    )
        except Exception as exc:
            print(f"Search error: {exc}")
            results = []

    return render(request, "search_results.html", {"query": query, "results": results})


def get_activity_logs(collection_name="activity_logs", limit=20, last_doc_id=None):
    try:
        logs_ref = db.collection(collection_name)
        if last_doc_id:
            last_doc = logs_ref.document(last_doc_id).get()
            query = (
                logs_ref.order_by("timestamp", direction=firestore.Query.DESCENDING)
                .start_after(last_doc)
                .limit(limit)
            )
        else:
            query = logs_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)

        logs = []
        last_doc = None
        for doc in query.stream():
            log_data = doc.to_dict()
            log_data["id"] = doc.id
            logs.append(log_data)
            last_doc = doc

        return logs, last_doc.id if last_doc else None
    except Exception as exc:
        print(f"Error fetching activity logs: {exc}")
        return [], None


def activity_logs_view(request):
    activity_logs, _ = get_activity_logs()
    return render(
        request,
        "activity_logs.html",
        {"page_title": "Activity Logs", "activity_logs": activity_logs},
    )


def customer_actions_view(request):
    try:
        docs = (
            db.collection("Customer_Actions")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(50)
            .stream()
        )

        actions = []
        for doc in docs:
            data = doc.to_dict()
            details = data.get("details", {})
            actions.append(
                {
                    "id": doc.id,
                    "action": data.get("action", ""),
                    "user": data.get("user", ""),
                    "timestamp": data.get("timestamp", ""),
                    "agentId": details.get("agentId", ""),
                    "enquiryTime": details.get("enquiryTime", ""),
                    "propertyId": details.get("propertyId", ""),
                    "propertyTitle": details.get("propertyTitle", ""),
                    "userMessage": details.get("userMessage", ""),
                }
            )
    except Exception as exc:
        print(f"Error fetching customer actions: {exc}")
        actions = []

    return render(
        request,
        "customer_actions.html",
        {"page_title": "Customer Activity Logs", "actions": actions},
    )


def contact_requests_view(request):
    requests_data = []
    try:
        docs = db.collection("contact_requests").order_by("timestamp", direction=firestore.Query.DESCENDING).stream()
        for doc in docs:
            data = doc.to_dict()
            requests_data.append(
                {
                    "id": doc.id,
                    "name": data.get("name", ""),
                    "phone": data.get("phone", ""),
                    "message": data.get("message", ""),
                    "listingType": data.get("listingType", ""),
                    "contactMethod": data.get("contactMethod", ""),
                    "timestamp": data.get("timestamp", ""),
                }
            )
    except Exception as exc:
        print("Error fetching contact requests:", exc)

    return render(
        request,
        "contact_requests.html",
        {"page_title": "Contact Requests", "requests": requests_data},
    )


def daily_visits_view(request):
    visits = []
    try:
        for doc in db.collection("dailyVisits").stream():
            data = doc.to_dict()
            visits.append(
                {
                    "id": doc.id,
                    "date": data.get("date", ""),
                    "lastVisit": data.get("lastVisit", ""),
                    "visitCount": data.get("visitCount", 0),
                }
            )
        visits.sort(key=lambda item: item["date"], reverse=True)
    except Exception as exc:
        print("Error fetching daily visits:", exc)

    return render(request, "daily_visits.html", {"page_title": "Daily Visit Logs", "visits": visits})


def leads_list(request):
    leads = fetch_collection("leads")
    for lead in leads:
        lead["lifecycle_stage"] = normalize_lead_status(lead.get("status"))
        lead["lead_score"] = lead_score(lead)

    return render(request, "leads_list.html", {"leads": leads})


def property_locations_view(request):
    if request.method == "POST" and "selected[]" in request.POST:
        for doc_id in request.POST.getlist("selected[]"):
            db.collection("property_location").document(doc_id).delete()
        messages.success(request, "Selected locations deleted.")
        return redirect("property_locations")

    properties_data = []
    try:
        for doc in db.collection("property_location").stream():
            data = doc.to_dict()
            created_at = data.get("createdAt")
            properties_data.append(
                {
                    "id": doc.id,
                    "location": data.get("location", "N/A"),
                    "imageurl": data.get("imageurl", ""),
                    "createdAt": created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else "",
                }
            )
    except Exception as exc:
        messages.error(request, f"Unable to load property locations: {exc}")

    return render(request, "property_locations.html", {"properties": properties_data})


@csrf_exempt
def update_property_location(request, doc_id):
    if request.method == "POST":
        try:
            update_data = {"location": request.POST.get("location")}
            imagefile = request.FILES.get("imagefile")

            if imagefile:
                temp_file_path = os.path.join(tempfile.gettempdir(), os.path.basename(imagefile.name))
                with open(temp_file_path, "wb+") as temp_file:
                    for chunk in imagefile.chunks():
                        temp_file.write(chunk)

                bucket = firebase_bucket()
                blob = bucket.blob(f"property_location_images/{os.path.basename(imagefile.name)}")
                blob.upload_from_filename(temp_file_path)
                blob.make_public()
                os.remove(temp_file_path)
                update_data["imageurl"] = blob.public_url

            db.collection("property_location").document(doc_id).update(clean_payload(update_data))
            return JsonResponse({"status": "success"})
        except Exception as exc:
            return JsonResponse({"status": "fail", "message": str(exc)})

    return JsonResponse({"status": "fail", "message": "Invalid request method"})


@csrf_exempt
def delete_property_location(request, doc_id):
    if request.method == "POST":
        try:
            db.collection("property_location").document(doc_id).delete()
            return JsonResponse({"status": "deleted"})
        except Exception as exc:
            return JsonResponse({"status": "fail", "message": str(exc)})

    return JsonResponse({"status": "fail", "message": "Invalid request method"})


@csrf_exempt
def add_property_location(request):
    if request.method == "POST":
        try:
            location = request.POST.get("location")
            imagefile = request.FILES.get("imagefile")
            if not location or not imagefile:
                return JsonResponse({"status": "fail", "message": "Location and image are required"})

            temp_file_path = os.path.join(tempfile.gettempdir(), os.path.basename(imagefile.name))
            with open(temp_file_path, "wb+") as temp_file:
                for chunk in imagefile.chunks():
                    temp_file.write(chunk)

            bucket = firebase_bucket()
            blob = bucket.blob(f"property_location_images/{os.path.basename(imagefile.name)}")
            blob.upload_from_filename(temp_file_path)
            blob.make_public()
            os.remove(temp_file_path)

            db.collection("property_location").add(
                {
                    "location": location,
                    "imageurl": blob.public_url,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                }
            )
            return JsonResponse({"status": "success"})
        except Exception as exc:
            return JsonResponse({"status": "fail", "message": str(exc)})

    return JsonResponse({"status": "fail", "message": "Invalid request method"})


def registration_list(request):
    registrations = fetch_collection("registrations")
    return render(request, "registrations.html", {"registrations": registrations})


def unfulfilled_searches(request):
    searches = fetch_collection("unfulfilled_searches")
    return render(request, "unfulfilled_searches.html", {"searches": searches})


def user_list(request):
    users = fetch_collection("users")
    return render(request, "user_list.html", {"users": users})


def deleted_properties_view(request):
    deleted_properties = fetch_collection("deleted_properties")
    for item in deleted_properties:
        item["doc_id"] = item.get("id")
    return render(request, "deleted_properties.html", {"properties": deleted_properties})


def add_property(request):
    if request.method == "POST":
        property_data = clean_payload(
            {
                "name": request.POST.get("name"),
                "location": request.POST.get("location"),
                "type": request.POST.get("type"),
                "subtype": request.POST.get("subtype"),
                "bhk": request.POST.get("bhk"),
                "sqft": request.POST.get("sqft"),
                "price": request.POST.get("price"),
                "plot_area": request.POST.get("plot_area"),
                "unit": request.POST.get("unit"),
                "listed_on": request.POST.get("listed_on"),
                "owner_name": request.POST.get("owner_name"),
                "phone_number": request.POST.get("phone_number"),
                "whatsapp_number": request.POST.get("whatsapp_number"),
                "agent": request.POST.get("agent"),
                "status": request.POST.get("status") or "Available",
                "pricing_options": request.POST.get("pricing_options"),
                "remarks": request.POST.get("remarks"),
                "property_description": request.POST.get("property_description"),
                "createdAt": firestore.SERVER_TIMESTAMP,
            }
        )

        images = save_uploaded_images(request.FILES.getlist("images"))
        if images:
            property_data["images"] = images

        try:
            db.collection("properties").add(property_data)
            messages.success(request, "Property added successfully.")
            return redirect("properties")
        except Exception as exc:
            messages.error(request, f"Unable to add property: {exc}")

    return render(request, "add_property.html")


def agentsproperties(request):
    agents_with_properties = []
    agents_data = api_get("/agents", default=[]) or []

    for agent in agents_data:
        agent_id = agent.get("agentId") or agent.get("AgentId")
        properties_data = api_get(f"/{agent_id}/properties", default=[]) if agent_id else []
        agents_with_properties.append(
            {
                "agentId": agent_id,
                "agentName": agent.get("name", agent.get("Name", "Unnamed Agent")),
                "email": agent.get("email", "N/A"),
                "properties": properties_data or [],
            }
        )

    return render(request, "agentsproperties.html", {"agents": agents_with_properties})


def crm_workspace(request):
    leads = fetch_collection("leads", limit=250)
    properties_data = fetch_collection("properties", limit=250)
    agents_data = fetch_collection("agents", limit=100)
    contact_requests = fetch_collection("contact_requests", limit=100)
    pipeline = build_pipeline(leads)

    scored_leads = []
    for lead in leads:
        lead["lead_score"] = lead_score(lead)
        lead["lifecycle_stage"] = normalize_lead_status(lead.get("status"))
        scored_leads.append(lead)
    scored_leads.sort(key=lambda item: item.get("lead_score", 0), reverse=True)

    context = {
        "pipeline_stages": pipeline,
        "scored_leads": scored_leads[:8],
        "properties_count": len(properties_data),
        "agents_count": len(agents_data),
        "contacts_count": len(leads) + len(contact_requests),
        "automation_playbooks": [
            {
                "name": "Speed-to-lead workflow",
                "trigger": "New enquiry or contact request",
                "actions": "Create contact, score lead, assign owner, schedule first touch",
                "impact": "Under 5 minute SLA",
            },
            {
                "name": "Property match nurture",
                "trigger": "Budget and location captured",
                "actions": "Recommend matching listings and notify assigned agent",
                "impact": "Higher site-visit conversion",
            },
            {
                "name": "Deal stage hygiene",
                "trigger": "No activity for 48 hours",
                "actions": "Create task, escalate stale deals, log follow-up outcome",
                "impact": "Cleaner pipeline forecast",
            },
        ],
        "hubspot_objects": [
            {"name": "Contacts", "detail": "Buyer, seller, tenant, investor lifecycle stages"},
            {"name": "Deals", "detail": "Real estate pipeline from enquiry to closure"},
            {"name": "Properties", "detail": "Custom object for listings, budgets, and matching"},
            {"name": "Activities", "detail": "Calls, WhatsApp, visits, notes, tasks, and emails"},
        ],
        "specialist_highlights": [
            "Lifecycle stage mapping",
            "Lead scoring and routing",
            "Workflow automation",
            "Dashboard reporting",
            "Data quality checks",
            "Property CRM custom object design",
        ],
    }
    return render(request, "crm_workspace.html", context)


@csrf_exempt
def update_agent(request, doc_id):
    if request.method == "POST":
        updated_data = {
            "Firstname": request.POST.get("Firstname"),
            "Lastname": request.POST.get("Lastname"),
            "Username": request.POST.get("Username"),
            "Email": request.POST.get("Email"),
            "Contactnumber": request.POST.get("Contactnumber"),
            "Age": int(request.POST.get("Age")) if request.POST.get("Age") else None,
            "Districtplace": request.POST.get("Districtplace"),
            "Allocatedlocations": [
                location.strip()
                for location in request.POST.get("Allocatedlocations", "").split(",")
                if location.strip()
            ],
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }

        if request.FILES.get("ProfileImage"):
            image = request.FILES["ProfileImage"]
            path = default_storage.save(
                f"agent_images/{os.path.basename(image.name)}",
                ContentFile(image.read()),
            )
            updated_data["imageUrl"] = default_storage.url(path)

        try:
            db.collection("agents").document(doc_id).update(clean_payload(updated_data))
            messages.success(request, "Agent updated successfully.")
        except Exception as exc:
            return HttpResponse(f"Update failed: {exc}", status=500)

    return redirect("agents")
