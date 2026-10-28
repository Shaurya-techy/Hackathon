# AICTE Complaint Intelligence Dashboard (Hackathon Prototype)

A locally runnable Flask + SQLite prototype for the problem statement **“Integrated Dashboard for Monitoring and Tracking Institution-Related Complaint Cases Across AICTE Bureaus/Cells.”**

This is an independent demo, not an official AICTE service. The default bureau/cell names and complaint records are illustrative placeholders. Replace them with the organizer-provided dataset and official workflow requirements before your final submission.

## Features included

- Sign-in with administrator and bureau/cell officer roles
- Dashboard cards for total, pending, resolved/closed, overdue, and high-priority cases
- Status distribution, monthly complaint activity, and department workload
- Complaint registration, search, filtering, details, and editing
- Assignment to a configured bureau/cell and matching demo officer
- Status transition validation and case activity timeline
- Due-date monitoring and overdue highlighting
- Department setup (administrator only)
- CSV import with common header mapping and row-level validation
- CSV export of the currently filtered complaint list
- JSON endpoints at `/api/complaints` and `/api/analytics`
- SQLite storage created automatically on first run
- CSRF tokens on form submissions and password hashes for demo users

## Requirements

- Python 3.10 or later recommended
- pip
- A current browser

## Run on Windows

Open Command Prompt or PowerShell inside this folder:

```powershell
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000** in your browser.

## Run on macOS / Linux

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5000**.

## Demo accounts

| Role | Username | Password |
|---|---|---|
| Administrator | `admin` | `Admin@123` |
| Bureau/cell officer | `officer` | `Officer@123` |
| Grievance officer | `officer2` | `Officer@123` |
| Academic officer | `officer3` | `Officer@123` |

These accounts are created automatically for an empty database. They are only for local demonstration. Do not publish or expose this prototype with default credentials. Change credentials and the `FLASK_SECRET_KEY`, disable debug mode, use HTTPS, and review permissions and deployment security before any shared deployment.

## Important workflow

1. Sign in as `admin`.
2. Review the sample dashboard and case register.
3. Open a case to view its activity timeline.
4. Assign the case to a department/officer and add a note.
5. Change the status using a valid transition and a required action note.
6. Register your own test complaint.
7. Import CSV data from **Import dataset** or download the example template.
8. Export filtered complaints from **All complaints**.

## CSV import format

The minimum recognized fields are `institution_name` and `subject` (common alternatives such as `institution`, `college`, and `title` are also accepted). Optional recognized columns include:

- `description`
- `category`
- `priority`
- `status`
- `department` or `bureau` or `cell`
- `complaint_number`
- `due_date` (prefer `YYYY-MM-DD`)
- `institution_email`

Unknown categories and statuses are normalized to safe defaults. A department is matched by its configured name or short code; unmatched values remain unassigned. Duplicate complaint numbers are skipped. Check the import summary after loading data.

## API endpoints

The API endpoints below require a signed-in account and honor officer visibility rules.

- `GET /api/complaints` — returns complaint records; supports filters such as `?status=New&q=college`
- `GET /api/analytics` — returns total, pending, resolved, and overdue counts
- `GET /health` — lightweight local health check

## Database and reset

The SQLite database is created at `instance/aicte_complaints.db`. Back it up before importing the real dataset. To reset the local demo, stop the server and remove that database file; the next launch recreates the schema and seeds demo data.

## Production/hackathon caveats

- Confirm the real bureau/cell list, authorized roles, status rules, due-date policy, and required case fields with the organizer.
- The app uses a simple local prototype architecture. Before deployment, set a strong secret through the `FLASK_SECRET_KEY` environment variable, use a production WSGI server, disable Flask debug mode, add rate limiting and security headers, configure backups, and review audit/data-retention requirements.
- Sample cases are fictional and must not be mistaken for official complaint records.
