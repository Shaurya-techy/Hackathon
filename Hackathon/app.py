"""AICTE integrated complaint monitoring dashboard - hackathon prototype.

Run locally with: python app.py
This demo uses illustrative departments and seeded sample complaints. It is not an
official AICTE system and should not be exposed publicly without deployment hardening.
"""
from __future__ import annotations

import csv
import io
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from db import close_db, get_db, init_db, seed_demo_data

STATUSES = ["New", "Assigned", "In Progress", "On Hold", "Resolved", "Closed", "Reopened"]
PRIORITIES = ["Low", "Medium", "High", "Critical"]
CATEGORIES = [
    "Administration", "Academic", "Grievance", "Institution Records",
    "Application", "Technical Support", "Policy Query", "Other",
]
CLOSED_STATUSES = ("Resolved", "Closed")
ACTIVE_STATUSES = ("New", "Assigned", "In Progress", "On Hold", "Reopened")
ALLOWED_TRANSITIONS = {
    "New": {"Assigned", "In Progress", "On Hold", "Resolved"},
    "Assigned": {"In Progress", "On Hold", "Resolved", "Reopened"},
    "In Progress": {"Assigned", "On Hold", "Resolved", "Reopened"},
    "On Hold": {"Assigned", "In Progress", "Resolved", "Reopened"},
    "Resolved": {"Closed", "Reopened"},
    "Closed": {"Reopened"},
    "Reopened": {"Assigned", "In Progress", "On Hold", "Resolved"},
}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "dev-only-change-this-secret"),
        DATABASE=os.path.join(app.instance_path, "aicte_complaints.db"),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        seed_demo_data()

    @app.before_request
    def load_user_and_validate_csrf():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = get_db().execute(
                "SELECT id, username, full_name, role, department_id, active FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if not g.user or not g.user["active"]:
                session.clear()
                g.user = None

        if request.method == "POST":
            expected = session.get("csrf_token")
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not expected or not supplied or not secrets.compare_digest(expected, supplied):
                abort(400, description="Your form session expired or could not be verified. Refresh the page and try again.")

    @app.context_processor
    def inject_template_helpers():
        def csrf_token() -> str:
            if "csrf_token" not in session:
                session["csrf_token"] = secrets.token_urlsafe(32)
            return session["csrf_token"]

        def field_value(record, key: str, default=""):
            if not record:
                return default
            try:
                value = record.get(key, default) if hasattr(record, "get") else record[key]
                return default if value is None else value
            except (KeyError, IndexError, TypeError):
                return default

        return {
            "csrf_token": csrf_token,
            "field_value": field_value,
            "STATUSES": STATUSES,
            "PRIORITIES": PRIORITIES,
            "CATEGORIES": CATEGORIES,
            "today_iso": date.today().isoformat(),
        }

    @app.template_filter("shortdate")
    def shortdate(value):
        if not value:
            return "—"
        try:
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value.strftime("%d %b %Y")
        except (ValueError, TypeError):
            return str(value)

    @app.template_filter("datetime_short")
    def datetime_short(value):
        if not value:
            return "—"
        try:
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value.strftime("%d %b %Y, %I:%M %p")
        except (ValueError, TypeError):
            return str(value)

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                flash("Please sign in to continue.", "info")
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] != "admin":
                abort(403, description="Administrator access is required for this action.")
            return view(*args, **kwargs)
        return wrapped

    def now_text() -> str:
        return datetime.now().replace(microsecond=0).isoformat(sep=" ")

    def generate_complaint_number() -> str:
        year = datetime.now().year
        # Random suffix keeps the identifier collision-resistant across concurrent requests.
        while True:
            number = f"CMP-{year}-{secrets.token_hex(3).upper()}"
            exists = get_db().execute(
                "SELECT 1 FROM complaints WHERE complaint_number = ?", (number,)
            ).fetchone()
            if not exists:
                return number

    def get_departments(active_only: bool = True):
        sql = "SELECT id, code, name, description, active FROM departments"
        if active_only:
            sql += " WHERE active = 1"
        return get_db().execute(sql + " ORDER BY name").fetchall()

    def add_case_update(complaint_id: int, action_type: str, comment: str,
                        from_status: str | None = None, to_status: str | None = None) -> None:
        get_db().execute(
            """INSERT INTO case_updates(complaint_id, user_id, action_type, from_status, to_status, comment, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (complaint_id, g.user["id"] if g.user else None, action_type,
             from_status, to_status, comment.strip() or action_type, now_text()),
        )

    def add_audit(action: str, entity_type: str, entity_id: int | None, details: str = "") -> None:
        get_db().execute(
            "INSERT INTO audit_logs(user_id, action, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (g.user["id"] if g.user else None, action, entity_type, entity_id, details, now_text()),
        )

    def can_access_complaint(complaint) -> bool:
        if not g.user:
            return False
        if g.user["role"] == "admin":
            return True
        return complaint["assigned_to"] == g.user["id"] or (
            complaint["department_id"] is not None and complaint["department_id"] == g.user["department_id"]
        )

    def get_complaint_or_404(complaint_id: int):
        complaint = get_db().execute(
            """SELECT c.*, d.name AS department_name, d.code AS department_code,
                      u.full_name AS officer_name, creator.full_name AS submitted_by_name
               FROM complaints c
               LEFT JOIN departments d ON d.id = c.department_id
               LEFT JOIN users u ON u.id = c.assigned_to
               LEFT JOIN users creator ON creator.id = c.submitted_by
               WHERE c.id = ?""",
            (complaint_id,),
        ).fetchone()
        if not complaint:
            abort(404, description="Complaint not found.")
        if not can_access_complaint(complaint):
            abort(403, description="You do not have permission to view this complaint.")
        return complaint

    def complaint_scope_sql() -> tuple[str, list]:
        if g.user["role"] == "admin":
            return "1=1", []
        return "(c.department_id = ? OR c.assigned_to = ?)", [g.user["department_id"], g.user["id"]]

    def filtered_complaints_query(include_filters: bool = True):
        scope, params = complaint_scope_sql()
        clauses = [scope]
        if include_filters:
            q = request.args.get("q", "").strip()
            status = request.args.get("status", "").strip()
            priority = request.args.get("priority", "").strip()
            department_id = request.args.get("department_id", "").strip()
            category = request.args.get("category", "").strip()
            overdue = request.args.get("overdue", "").strip()
            if q:
                like = f"%{q}%"
                clauses.append("(c.complaint_number LIKE ? OR c.institution_name LIKE ? OR c.subject LIKE ? OR c.description LIKE ?)")
                params.extend([like, like, like, like])
            if status in STATUSES:
                clauses.append("c.status = ?")
                params.append(status)
            if priority in PRIORITIES:
                clauses.append("c.priority = ?")
                params.append(priority)
            if category in CATEGORIES:
                clauses.append("c.category = ?")
                params.append(category)
            if department_id.isdigit():
                clauses.append("c.department_id = ?")
                params.append(int(department_id))
            if overdue == "1":
                clauses.append("c.due_date < ? AND c.status NOT IN ('Resolved', 'Closed')")
                params.append(date.today().isoformat())
        sql = """SELECT c.*, d.name AS department_name, u.full_name AS officer_name
                 FROM complaints c LEFT JOIN departments d ON d.id = c.department_id
                 LEFT JOIN users u ON u.id = c.assigned_to
                 WHERE """ + " AND ".join(clauses) + " ORDER BY c.created_at DESC, c.id DESC"
        return get_db().execute(sql, params).fetchall()

    @app.route("/")
    def index():
        return redirect(url_for("dashboard") if g.user else url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ? AND active = 1", (username,)
            ).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                flash(f"Welcome back, {user['full_name']}.", "success")
                return redirect(url_for("dashboard"))
            flash("Username or password was incorrect.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        session.clear()
        flash("You have been signed out.", "info")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        scope, scope_params = complaint_scope_sql()
        total = db.execute(f"SELECT COUNT(*) FROM complaints c WHERE {scope}", scope_params).fetchone()[0]
        pending_scope = scope + " AND c.status NOT IN ('Resolved', 'Closed')"
        pending = db.execute(f"SELECT COUNT(*) FROM complaints c WHERE {pending_scope}", scope_params).fetchone()[0]
        resolved_scope = scope + " AND c.status IN ('Resolved', 'Closed')"
        resolved = db.execute(f"SELECT COUNT(*) FROM complaints c WHERE {resolved_scope}", scope_params).fetchone()[0]
        overdue_scope = scope + " AND c.due_date < ? AND c.status NOT IN ('Resolved', 'Closed')"
        overdue_params = scope_params + [date.today().isoformat()]
        overdue = db.execute(f"SELECT COUNT(*) FROM complaints c WHERE {overdue_scope}", overdue_params).fetchone()[0]
        high_scope = scope + " AND c.priority IN ('High', 'Critical') AND c.status NOT IN ('Resolved', 'Closed')"
        high_priority = db.execute(f"SELECT COUNT(*) FROM complaints c WHERE {high_scope}", scope_params).fetchone()[0]

        status_rows = db.execute(
            f"SELECT c.status, COUNT(*) AS count FROM complaints c WHERE {scope} GROUP BY c.status",
            scope_params,
        ).fetchall()
        status_counts = {status: 0 for status in STATUSES}
        for row in status_rows:
            status_counts[row["status"]] = row["count"]

        department_rows = db.execute(
            f"""SELECT d.name, COUNT(c.id) AS count
                FROM departments d LEFT JOIN complaints c ON c.department_id = d.id
                WHERE d.active = 1 AND ({scope.replace('c.', 'c.')})
                GROUP BY d.id, d.name ORDER BY count DESC, d.name LIMIT 6""",
            scope_params,
        ).fetchall()
        # Monthly trend for the current month and previous five months.
        monthly = []
        today = date.today().replace(day=1)
        months = []
        for offset in range(5, -1, -1):
            year, month = today.year, today.month - offset
            while month <= 0:
                year -= 1
                month += 12
            months.append(date(year, month, 1))
        for month_start in months:
            if month_start.month == 12:
                month_end = date(month_start.year + 1, 1, 1)
            else:
                month_end = date(month_start.year, month_start.month + 1, 1)
            count = db.execute(
                f"SELECT COUNT(*) FROM complaints c WHERE {scope} AND date(c.created_at) >= ? AND date(c.created_at) < ?",
                scope_params + [month_start.isoformat(), month_end.isoformat()],
            ).fetchone()[0]
            monthly.append({"label": month_start.strftime("%b"), "count": count})

        recent = db.execute(
            f"""SELECT c.*, d.name AS department_name, u.full_name AS officer_name
                FROM complaints c LEFT JOIN departments d ON d.id = c.department_id
                LEFT JOIN users u ON u.id = c.assigned_to WHERE {scope}
                ORDER BY c.created_at DESC, c.id DESC LIMIT 7""",
            scope_params,
        ).fetchall()
        return render_template(
            "dashboard.html", total=total, pending=pending, resolved=resolved,
            overdue=overdue, high_priority=high_priority, status_counts=status_counts,
            department_rows=department_rows, monthly=monthly, recent=recent,
        )

    @app.route("/complaints")
    @login_required
    def complaints():
        records = filtered_complaints_query()
        return render_template(
            "complaints.html", complaints=records, departments=get_departments(),
            selected={key: request.args.get(key, "") for key in ("q", "status", "priority", "department_id", "category", "overdue")},
        )

    @app.route("/complaints/new", methods=["GET", "POST"])
    @login_required
    def new_complaint():
        departments = get_departments()
        if g.user["role"] != "admin":
            departments = [dept for dept in departments if dept["id"] == g.user["department_id"]]
        if request.method == "POST":
            institution = request.form.get("institution_name", "").strip()
            subject = request.form.get("subject", "").strip()
            description = request.form.get("description", "").strip()
            email = request.form.get("institution_email", "").strip()
            category = request.form.get("category", "Other").strip()
            priority = request.form.get("priority", "Medium").strip()
            department_raw = request.form.get("department_id", "").strip()
            due_date = request.form.get("due_date", "").strip() or None
            errors = []
            if not institution or len(institution) > 200:
                errors.append("Institution name is required and must be under 200 characters.")
            if not subject or len(subject) > 250:
                errors.append("Subject is required and must be under 250 characters.")
            if not description:
                errors.append("Complaint description is required.")
            if category not in CATEGORIES:
                errors.append("Select a valid complaint category.")
            if priority not in PRIORITIES:
                errors.append("Select a valid priority.")
            department_id = int(department_raw) if department_raw.isdigit() else None
            if g.user["role"] != "admin" and not department_id:
                department_id = g.user["department_id"]
            if department_raw and not department_id:
                errors.append("Select a valid bureau/cell.")
            if department_id and not get_db().execute("SELECT id FROM departments WHERE id=? AND active=1", (department_id,)).fetchone():
                errors.append("The selected bureau/cell is unavailable.")
            if g.user["role"] != "admin" and department_id != g.user["department_id"]:
                errors.append("Bureau/cell officers can only register a case within their assigned bureau/cell.")
            if due_date:
                try:
                    date.fromisoformat(due_date)
                except ValueError:
                    errors.append("Due date must be a valid date.")
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("complaint_form.html", complaint=request.form, departments=departments, page_title="Register complaint")

            now = now_text()
            number = generate_complaint_number()
            cursor = get_db().execute(
                """INSERT INTO complaints(
                    complaint_number, institution_name, institution_email, subject, description,
                    category, priority, status, department_id, submitted_by, created_at, updated_at, due_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'New', ?, ?, ?, ?, ?)""",
                (number, institution, email, subject, description, category, priority,
                 department_id, g.user["id"], now, now, due_date),
            )
            complaint_id = cursor.lastrowid
            add_case_update(complaint_id, "Created", "Complaint registered.", None, "New")
            add_audit("created", "complaint", complaint_id, number)
            get_db().commit()
            flash(f"Complaint {number} was registered successfully.", "success")
            return redirect(url_for("complaint_detail", complaint_id=complaint_id))
        initial_values = {"department_id": g.user["department_id"]} if g.user["role"] != "admin" else {}
        return render_template("complaint_form.html", complaint=initial_values, departments=departments, page_title="Register complaint")

    @app.route("/complaints/<int:complaint_id>")
    @login_required
    def complaint_detail(complaint_id):
        complaint = get_complaint_or_404(complaint_id)
        updates = get_db().execute(
            """SELECT cu.*, u.full_name AS user_name FROM case_updates cu
               LEFT JOIN users u ON u.id = cu.user_id
               WHERE cu.complaint_id = ? ORDER BY cu.created_at DESC, cu.id DESC""",
            (complaint_id,),
        ).fetchall()
        departments = get_departments()
        officers = get_db().execute(
            "SELECT id, full_name, department_id FROM users WHERE role='officer' AND active=1 ORDER BY full_name"
        ).fetchall()
        return render_template("complaint_detail.html", complaint=complaint, updates=updates,
                               departments=departments, officers=officers)

    @app.route("/complaints/<int:complaint_id>/edit", methods=["GET", "POST"])
    @login_required
    def edit_complaint(complaint_id):
        complaint = get_complaint_or_404(complaint_id)
        if request.method == "POST":
            institution = request.form.get("institution_name", "").strip()
            subject = request.form.get("subject", "").strip()
            description = request.form.get("description", "").strip()
            email = request.form.get("institution_email", "").strip()
            category = request.form.get("category", "Other").strip()
            priority = request.form.get("priority", "Medium").strip()
            due_date = request.form.get("due_date", "").strip() or None
            errors = []
            if not institution or len(institution) > 200:
                errors.append("Institution name is required and must be under 200 characters.")
            if not subject or len(subject) > 250:
                errors.append("Subject is required and must be under 250 characters.")
            if not description:
                errors.append("Description is required.")
            if category not in CATEGORIES:
                errors.append("Select a valid category.")
            if priority not in PRIORITIES:
                errors.append("Select a valid priority.")
            if due_date:
                try:
                    date.fromisoformat(due_date)
                except ValueError:
                    errors.append("Due date must be a valid date.")
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("complaint_form.html", complaint=request.form, departments=get_departments(), page_title="Edit complaint", edit_mode=True, complaint_id=complaint_id)
            db = get_db()
            db.execute(
                """UPDATE complaints SET institution_name=?, institution_email=?, subject=?, description=?,
                   category=?, priority=?, due_date=?, updated_at=? WHERE id=?""",
                (institution, email, subject, description, category, priority, due_date, now_text(), complaint_id),
            )
            add_case_update(complaint_id, "Edited", "Complaint details updated.", complaint["status"], complaint["status"])
            add_audit("updated", "complaint", complaint_id, "Complaint details edited")
            db.commit()
            flash("Complaint details updated.", "success")
            return redirect(url_for("complaint_detail", complaint_id=complaint_id))
        return render_template("complaint_form.html", complaint=complaint, departments=get_departments(), page_title="Edit complaint", edit_mode=True, complaint_id=complaint_id)

    @app.route("/complaints/<int:complaint_id>/assign", methods=["POST"])
    @login_required
    def assign_complaint(complaint_id):
        complaint = get_complaint_or_404(complaint_id)
        department_raw = request.form.get("department_id", "").strip()
        officer_raw = request.form.get("assigned_to", "").strip()
        note = request.form.get("comment", "").strip() or "Complaint assignment updated."
        department_id = int(department_raw) if department_raw.isdigit() else None
        officer_id = int(officer_raw) if officer_raw.isdigit() else None
        if not department_id or not get_db().execute("SELECT id FROM departments WHERE id=? AND active=1", (department_id,)).fetchone():
            flash("Select a valid bureau/cell.", "error")
            return redirect(url_for("complaint_detail", complaint_id=complaint_id))
        officer = None
        if officer_id:
            officer = get_db().execute("SELECT id, full_name, department_id FROM users WHERE id=? AND role='officer' AND active=1", (officer_id,)).fetchone()
            if not officer or officer["department_id"] != department_id:
                flash("Choose an active officer belonging to the selected bureau/cell.", "error")
                return redirect(url_for("complaint_detail", complaint_id=complaint_id))
        if g.user["role"] != "admin" and (department_id != g.user["department_id"] or (officer_id and officer_id != g.user["id"])):
            abort(403, description="You may only assign cases within your authorized bureau/cell.")
        new_status = complaint["status"]
        if complaint["status"] == "New":
            new_status = "Assigned"
        get_db().execute(
            "UPDATE complaints SET department_id=?, assigned_to=?, status=?, updated_at=? WHERE id=?",
            (department_id, officer_id, new_status, now_text(), complaint_id),
        )
        add_case_update(complaint_id, "Assignment", note, complaint["status"], new_status)
        add_audit("assigned", "complaint", complaint_id, f"department_id={department_id}; officer_id={officer_id}")
        get_db().commit()
        flash("Complaint assignment updated.", "success")
        return redirect(url_for("complaint_detail", complaint_id=complaint_id))

    @app.route("/complaints/<int:complaint_id>/status", methods=["POST"])
    @login_required
    def update_status(complaint_id):
        complaint = get_complaint_or_404(complaint_id)
        new_status = request.form.get("status", "").strip()
        comment = request.form.get("comment", "").strip()
        if new_status not in STATUSES:
            flash("Select a valid status.", "error")
        elif new_status == complaint["status"]:
            flash("The complaint already has that status.", "info")
        elif new_status not in ALLOWED_TRANSITIONS.get(complaint["status"], set()):
            flash(f"Status change from {complaint['status']} to {new_status} is not allowed.", "error")
        elif not comment:
            flash("Add a note describing the action before updating status.", "error")
        else:
            if new_status == "Resolved":
                resolved_at = now_text()
            elif new_status == "Closed":
                resolved_at = complaint["resolved_at"] or now_text()
            elif new_status == "Reopened":
                resolved_at = None
            else:
                resolved_at = complaint["resolved_at"]
            get_db().execute(
                "UPDATE complaints SET status=?, updated_at=?, resolved_at=? WHERE id=?",
                (new_status, now_text(), resolved_at, complaint_id),
            )
            add_case_update(complaint_id, "Status", comment, complaint["status"], new_status)
            add_audit("status_changed", "complaint", complaint_id, f"{complaint['status']} -> {new_status}")
            get_db().commit()
            flash(f"Complaint status changed to {new_status}.", "success")
        return redirect(url_for("complaint_detail", complaint_id=complaint_id))

    @app.route("/departments", methods=["GET", "POST"])
    @admin_required
    def departments():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            code = request.form.get("code", "").strip().upper().replace(" ", "-")
            description = request.form.get("description", "").strip()
            if not name or not code:
                flash("Department name and code are required.", "error")
            elif len(name) > 120 or len(code) > 20:
                flash("Department name or code is too long.", "error")
            else:
                try:
                    get_db().execute(
                        "INSERT INTO departments(code, name, description, active, created_at) VALUES (?, ?, ?, 1, ?)",
                        (code, name, description, now_text()),
                    )
                    add_audit("created", "department", None, f"{code}: {name}")
                    get_db().commit()
                    flash("Department added successfully.", "success")
                    return redirect(url_for("departments"))
                except sqlite3.IntegrityError:
                    get_db().rollback()
                    flash("That department code or name already exists.", "error")
        rows = get_db().execute(
            """SELECT d.*, COUNT(DISTINCT c.id) AS complaint_count,
                      COUNT(DISTINCT u.id) AS officer_count
               FROM departments d
               LEFT JOIN complaints c ON c.department_id=d.id
               LEFT JOIN users u ON u.department_id=d.id AND u.role='officer' AND u.active=1
               GROUP BY d.id ORDER BY d.active DESC, d.name"""
        ).fetchall()
        return render_template("departments.html", departments=rows)

    @app.route("/imports", methods=["GET", "POST"])
    @admin_required
    def imports():
        results = None
        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename:
                flash("Choose a CSV file to import.", "error")
            elif not secure_filename(upload.filename).lower().endswith(".csv"):
                flash("Only CSV files are supported.", "error")
            else:
                try:
                    text = upload.stream.read(MAX_UPLOAD_BYTES + 1).decode("utf-8-sig")
                    if len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
                        raise ValueError("The upload exceeds the 5 MB limit.")
                    reader = csv.DictReader(io.StringIO(text))
                    if not reader.fieldnames:
                        raise ValueError("The CSV file is empty or has no header row.")
                    aliases = {
                        "institution_name": {"institution_name", "institution name", "institution", "college", "college_name", "name of institution"},
                        "subject": {"subject", "title", "complaint_subject", "complaint title"},
                        "description": {"description", "complaint_description", "complaint details", "details"},
                        "category": {"category", "complaint_category", "type"},
                        "priority": {"priority", "severity"},
                        "status": {"status", "case_status"},
                        "department": {"department", "bureau", "cell", "bureau/cell", "department_name"},
                        "complaint_number": {"complaint_number", "complaint id", "case_id", "case id"},
                        "due_date": {"due_date", "due date", "deadline"},
                        "institution_email": {"institution_email", "email", "contact_email"},
                    }
                    normalized_headers = {header.strip().lower(): header for header in reader.fieldnames if header}
                    mapped = {}
                    for canonical, names in aliases.items():
                        for alias in names:
                            if alias in normalized_headers:
                                mapped[canonical] = normalized_headers[alias]
                                break
                    if "institution_name" not in mapped or "subject" not in mapped:
                        raise ValueError("CSV needs at least institution_name (or institution/college) and subject (or title) columns.")
                    success_count, skipped_count = 0, 0
                    row_errors = []
                    db = get_db()
                    dept_rows = db.execute("SELECT id, name, code FROM departments WHERE active=1").fetchall()
                    dept_lookup = {r["name"].strip().lower(): r["id"] for r in dept_rows}
                    dept_lookup.update({r["code"].strip().lower(): r["id"] for r in dept_rows})
                    current_user = g.user["id"]
                    for row_num, row in enumerate(reader, start=2):
                        def value(key, default=""):
                            source = mapped.get(key)
                            return (row.get(source) or default).strip() if source else default
                        institution = value("institution_name")
                        subject = value("subject")
                        description = value("description", "No description provided in the source file.")
                        if not institution or not subject:
                            skipped_count += 1
                            row_errors.append(f"Row {row_num}: institution and subject are required.")
                            continue
                        status = value("status", "New")
                        if status not in STATUSES:
                            status = "New"
                        priority = value("priority", "Medium")
                        if priority not in PRIORITIES:
                            priority = "Medium"
                        category = value("category", "Other")
                        if category not in CATEGORIES:
                            category = "Other"
                        department_name = value("department").lower()
                        department_id = dept_lookup.get(department_name)
                        due_date = value("due_date") or None
                        if due_date:
                            try:
                                due_date = date.fromisoformat(due_date[:10]).isoformat()
                            except ValueError:
                                due_date = None
                        incoming_number = value("complaint_number")
                        if incoming_number and db.execute("SELECT id FROM complaints WHERE complaint_number=?", (incoming_number,)).fetchone():
                            skipped_count += 1
                            row_errors.append(f"Row {row_num}: complaint number {incoming_number} already exists.")
                            continue
                        number = incoming_number or generate_complaint_number()
                        now = now_text()
                        try:
                            cursor = db.execute(
                                """INSERT INTO complaints(complaint_number, institution_name, institution_email, subject,
                                   description, category, priority, status, department_id, submitted_by,
                                   created_at, updated_at, due_date, resolved_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (number, institution, value("institution_email"), subject, description,
                                 category, priority, status, department_id, current_user,
                                 now, now, due_date, now if status in CLOSED_STATUSES else None),
                            )
                            add_case_update(cursor.lastrowid, "Imported", "Case imported from CSV dataset.", None, status)
                            db.commit()
                            success_count += 1
                        except sqlite3.IntegrityError:
                            db.rollback()
                            skipped_count += 1
                            row_errors.append(f"Row {row_num}: duplicate or invalid record skipped.")
                    results = {"success": success_count, "skipped": skipped_count, "errors": row_errors[:20]}
                    if success_count:
                        add_audit("imported", "dataset", None, f"Imported {success_count} complaints")
                        db.commit()
                    flash(f"Import complete: {success_count} added, {skipped_count} skipped.", "success" if success_count else "info")
                except (UnicodeDecodeError, csv.Error, ValueError) as exc:
                    flash(f"Could not import file: {exc}", "error")
        return render_template("imports.html", results=results)

    @app.route("/export/complaints.csv")
    @login_required
    def export_complaints():
        records = filtered_complaints_query()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow([
            "complaint_number", "institution_name", "institution_email", "subject", "description",
            "category", "priority", "status", "department", "assigned_officer", "created_at", "due_date", "resolved_at"
        ])
        for row in records:
            writer.writerow([
                row["complaint_number"], row["institution_name"], row["institution_email"], row["subject"],
                row["description"], row["category"], row["priority"], row["status"],
                row["department_name"], row["officer_name"], row["created_at"], row["due_date"], row["resolved_at"]
            ])
        binary = io.BytesIO(output.getvalue().encode("utf-8-sig"))
        binary.seek(0)
        return send_file(binary, mimetype="text/csv", as_attachment=True, download_name="aicte_complaints_export.csv")

    @app.route("/reports")
    @login_required
    def reports():
        scope, params = complaint_scope_sql()
        db = get_db()
        category_rows = db.execute(
            f"SELECT c.category, COUNT(*) AS count FROM complaints c WHERE {scope} GROUP BY c.category ORDER BY count DESC",
            params,
        ).fetchall()
        department_rows = db.execute(
            f"""WITH scoped AS (SELECT c.* FROM complaints c WHERE {scope})
                SELECT d.name, COUNT(c.id) AS count,
                       SUM(CASE WHEN c.status NOT IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN c.due_date < ? AND c.status NOT IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS overdue
                FROM departments d LEFT JOIN scoped c ON c.department_id=d.id
                WHERE d.active=1 GROUP BY d.id ORDER BY count DESC""",
            params + [date.today().isoformat()],
        ).fetchall()
        return render_template("reports.html", category_rows=category_rows, department_rows=department_rows)

    @app.route("/api/complaints")
    @login_required
    def api_complaints():
        records = filtered_complaints_query()
        return jsonify({"count": len(records), "complaints": [
            {
                "id": row["id"], "complaint_number": row["complaint_number"],
                "institution_name": row["institution_name"], "subject": row["subject"],
                "category": row["category"], "priority": row["priority"], "status": row["status"],
                "department": row["department_name"], "assigned_officer": row["officer_name"],
                "created_at": row["created_at"], "due_date": row["due_date"],
            } for row in records
        ]})

    @app.route("/api/analytics")
    @login_required
    def api_analytics():
        scope, params = complaint_scope_sql()
        db = get_db()
        totals = db.execute(
            f"""SELECT COUNT(*) AS total,
                SUM(CASE WHEN c.status NOT IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN c.status IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN c.due_date < ? AND c.status NOT IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS overdue
                FROM complaints c WHERE {scope}""",
            [date.today().isoformat()] + params,
        ).fetchone()
        return jsonify({key: (totals[key] or 0) for key in ("total", "pending", "resolved", "overdue")})

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "AICTE Complaint Dashboard"})

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=getattr(error, "description", "Bad request.")), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message=getattr(error, "description", "Access denied.")), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message=getattr(error, "description", "Page not found.")), 404

    @app.errorhandler(413)
    def too_large(_error):
        return render_template("error.html", code=413, message="Upload is too large. Maximum file size is 5 MB."), 413

    return app


app = create_app()

if __name__ == "__main__":
    # Local development only. Use a production WSGI server for deployment.
    app.run(host="127.0.0.1", port=5000, debug=True)
