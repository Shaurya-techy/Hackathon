"""SQLite helpers and schema initialization for the AICTE complaint dashboard."""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = current_app.config["DATABASE"]
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        connection = sqlite3.connect(db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        g.db = connection
    return g.db


def close_db(_error=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'officer')),
            department_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(department_id) REFERENCES departments(id)
        );

        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_number TEXT NOT NULL UNIQUE,
            institution_name TEXT NOT NULL,
            institution_email TEXT DEFAULT '',
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Other',
            priority TEXT NOT NULL DEFAULT 'Medium'
                CHECK(priority IN ('Low', 'Medium', 'High', 'Critical')),
            status TEXT NOT NULL DEFAULT 'New'
                CHECK(status IN ('New', 'Assigned', 'In Progress', 'On Hold', 'Resolved', 'Closed', 'Reopened')),
            department_id INTEGER,
            assigned_to INTEGER,
            submitted_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            due_date TEXT,
            resolved_at TEXT,
            FOREIGN KEY(department_id) REFERENCES departments(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id),
            FOREIGN KEY(submitted_by) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS case_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL,
            user_id INTEGER,
            action_type TEXT NOT NULL DEFAULT 'Comment',
            from_status TEXT,
            to_status TEXT,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(complaint_id) REFERENCES complaints(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            details TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
        CREATE INDEX IF NOT EXISTS idx_complaints_department ON complaints(department_id);
        CREATE INDEX IF NOT EXISTS idx_complaints_created ON complaints(created_at);
        CREATE INDEX IF NOT EXISTS idx_complaints_due_date ON complaints(due_date);
        CREATE INDEX IF NOT EXISTS idx_case_updates_complaint ON case_updates(complaint_id, created_at);
        """
    )
    db.commit()


def seed_demo_data() -> None:
    """Seed demonstrative departments, accounts and complaints for a fresh database."""
    db = get_db()
    now = datetime.now().replace(microsecond=0)
    now_text = now.isoformat(sep=" ")

    if db.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
        departments = [
            ("AIM", "Approval & Institution Monitoring", "Illustrative department for institution-related approvals and monitoring."),
            ("GRV", "Grievance Cell", "Illustrative team for grievance handling and follow-up."),
            ("ACP", "Academic Planning", "Illustrative team for academic planning and related matters."),
            ("ADM", "Administration Cell", "Illustrative team for administrative matters."),
            ("TEC", "Technical Education Support", "Illustrative team for technical education support."),
        ]
        for code, name, description in departments:
            db.execute(
                "INSERT INTO departments(code, name, description, created_at) VALUES (?, ?, ?, ?)",
                (code, name, description, now_text),
            )
        db.commit()

    departments = db.execute("SELECT id, code, name FROM departments ORDER BY id").fetchall()
    dept_by_code = {row["code"]: row["id"] for row in departments}

    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        users = [
            ("admin", "Admin@123", "System Administrator", "admin", None),
            ("officer", "Officer@123", "Demo Bureau Officer", "officer", dept_by_code.get("AIM")),
            ("officer2", "Officer@123", "Demo Grievance Officer", "officer", dept_by_code.get("GRV")),
            ("officer3", "Officer@123", "Demo Academic Officer", "officer", dept_by_code.get("ACP")),
        ]
        for username, password, full_name, role, department_id in users:
            db.execute(
                """INSERT INTO users(username, password_hash, full_name, role, department_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username, generate_password_hash(password), full_name, role, department_id, now_text),
            )
        db.commit()

    if db.execute("SELECT COUNT(*) FROM complaints").fetchone()[0] > 0:
        return

    admin_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    officers = {
        row["department_id"]: row["id"]
        for row in db.execute("SELECT id, department_id FROM users WHERE role='officer' AND department_id IS NOT NULL").fetchall()
    }
    examples = [
        ("Example Institute of Technology", "Administrative complaint regarding records", "Request to review an administrative record and provide an update.", "Administration", "High", "In Progress", "ADM", 16, -4),
        ("North Valley Engineering College", "Delayed grievance response", "The institution reports that a previously submitted grievance has not received a status update.", "Grievance", "Critical", "Assigned", "GRV", 8, -2),
        ("Greenfield Technical Campus", "Institution profile correction", "Request to correct a non-sensitive profile field in the sample institutional record.", "Institution Records", "Medium", "New", "AIM", 3, 10),
        ("Riverbend College of Engineering", "Academic calendar clarification", "Clarification requested about academic planning information.", "Academic", "Low", "Resolved", "ACP", 28, -12),
        ("Sunrise Institute", "Follow-up on application status", "The institution requests an update about an application-related matter.", "Application", "Medium", "In Progress", "AIM", 12, -1),
        ("Metro Technical College", "Incorrect institutional details", "Request to review inconsistent details in a demonstration record.", "Institution Records", "High", "On Hold", "AIM", 21, 4),
        ("Lakeside Engineering Institute", "Portal access support", "The institution requests technical assistance with portal access.", "Technical Support", "Medium", "Assigned", "TEC", 6, 2),
        ("Pioneer College of Technology", "Pending administrative clarification", "Clarification is required before the case can proceed.", "Administration", "High", "Reopened", "ADM", 19, -3),
        ("Hillcrest Institute", "Grievance closure confirmation", "Request to confirm that a previously resolved matter has been recorded correctly.", "Grievance", "Low", "Closed", "GRV", 35, -15),
        ("Coastal School of Engineering", "Missing report attachment", "The institution reports an attachment issue in a sample submission.", "Technical Support", "Medium", "New", "TEC", 2, 7),
        ("Central Polytechnic Institute", "Policy information request", "The institution requests clarification on an administrative policy.", "Policy Query", "Low", "Resolved", "ACP", 40, -18),
        ("Westgate Engineering College", "Update requested on case", "The institution is requesting a progress update on a pending matter.", "Grievance", "High", "In Progress", "GRV", 11, -5),
    ]
    for index, item in enumerate(examples, start=1):
        institution, subject, description, category, priority, status, dept_code, age_days, due_offset = item
        dept_id = dept_by_code.get(dept_code)
        created = now - timedelta(days=age_days)
        due = date.today() + timedelta(days=due_offset)
        resolved_at = (created + timedelta(days=3)).isoformat(sep=" ") if status in ("Resolved", "Closed") else None
        complaint_number = f"CMP-{now.year}-{index:04d}"
        cursor = db.execute(
            """INSERT INTO complaints(
                    complaint_number, institution_name, subject, description, category,
                    priority, status, department_id, assigned_to, submitted_by,
                    created_at, updated_at, due_date, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                complaint_number, institution, subject, description, category, priority, status,
                dept_id, officers.get(dept_id) if status != "New" else None, admin_id,
                created.isoformat(sep=" "), now_text, due.isoformat(), resolved_at,
            ),
        )
        if status != "New":
            db.execute(
                """INSERT INTO case_updates(complaint_id, user_id, action_type, from_status, to_status, comment, created_at)
                   VALUES (?, ?, 'Status', NULL, ?, ?, ?)""",
                (cursor.lastrowid, admin_id, status, "Demo case imported into the sample dashboard.", created.isoformat(sep=" ")),
            )
    db.commit()
