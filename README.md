# Finora

Finora is a full-stack personal finance and expense tracking platform built with Flask and MySQL that provides real-time income and expense management, multi-account ledger balances, per-category budgets, savings goals, peer settlements, and automated reporting.

---

## Overview

Managing personal finances across multiple checking, savings, and credit accounts often leads to fragmented tracking, manual errors, and unclear net-worth insights. Finora consolidates daily transactions, recurring payments, peer settlements, and savings goals into a single self-hosted dashboard.

### Who It Is For
- **Individuals** looking for a private, self-hosted expense manager.
- **Freelancers & Household Users** tracking multiple income sources and peer balances.
- **Developers & Recruiter Reviewers** evaluating clean Flask architecture, service-layer patterns, and strict security controls.

### Main User Workflow
1. **Authenticate**: Log in using local credentials or Google OAuth 2.0.
2. **Setup Accounts**: Create checking, savings, or credit accounts to organize funds.
3. **Log Transactions**: Add expenses and income via slide-over drawers or CSV import.
4. **Monitor Budgets & Goals**: Set per-category monthly spending caps and contribute to savings goals.
5. **Manage Settlements**: Track payables ("I owe them") and receivables ("They owe me") with atomic balance updates.
6. **Export & Report**: Generate downloadable PDF reports or filtered CSV exports.

---

## Features

- **Authentication & Security**
  - Local email/password registration and login with bcrypt hashing.
  - Google OAuth 2.0 single sign-on integration via Authlib.
  - Enumeration-safe password reset flow delivering tokenized links via Gmail SMTP.
  - Account data reset and permanent account deletion behind password re-verification.

- **Financial Ledger & Accounts**
  - Multi-account management (Checking, Savings, Credit Cards, Cash).
  - Real-time balance calculations with atomic SQL updates.
  - Account archiving and restoration support.

- **Expense & Income Tracking**
  - Income and expense tracking with slide-over drawer UI overlays.
  - Search, category filter, date-range filtering, and pagination.
  - Summary metrics: total spending/income, monthly averages, top category/source.

- **Recurring Payments & Salaries**
  - Automated recurring subscription expense auto-charging.
  - Recurring salary/income paycheck scheduling.
  - Idempotent payment processing preventing duplicate charges.

- **Budgets & Notifications**
  - Per-category spending caps (e.g. Food, Transportation, Entertainment) and overall monthly budgets.
  - Real-time budget progress bars and over-budget alert indicators.
  - In-app Notification Center with unread badges and notification preferences.

- **Savings Goals**
  - Goal tracking with target dates, target amounts, and progress indicators.
  - Contribution history logs for tracking progress over time.
  - Goal closure (`CLOSED` badge) and goal restoration flow.

- **Balances & Peer Settlements**
  - Peer balance ledger for tracking debts ("I owe them" vs "They owe me").
  - Settlement action (`POST /settle/<id>`) that updates account balances without double-counting expenses.
  - Clean settlement reopen and deletion workflows.

- **CSV Import & Export**
  - CSV file parser supporting custom date formats and column mapping.
  - Interactive preview table with client/server validation and duplicate detection.
  - Informational row ID mapping that preserves database primary key integrity.
  - Filtered CSV exports preserving active date and category search filters.

- **PDF & Monthly Reports**
  - Downloadable PDF financial statements generated with ReportLab.
  - Monthly breakdown reports with category charts and spending insights.

---

## Tech Stack

- **Backend**: Python 3.13, Flask 3.x, Flask-WTF (CSRF Protection), Authlib (OAuth 2.0), ReportLab (PDF Generation)
- **Database**: MySQL 8.0, `mysql-connector-python` with connection pooling
- **Frontend**: Jinja2 HTML Templates, Tailwind CSS (via CDN), Geist Font, Material Symbols Icons, Vanilla JavaScript
- **Testing & CI**: `pytest` 9.x, `pytest-cov`, GitHub Actions

---

## Architecture

Finora follows a modular Flask architecture separating HTTP blueprints, domain services, database connection pooling, and Jinja templates.

```text
expense-tracker/
├── app.py                     # Application factory, blueprint registration & error handlers
├── config.py                  # Environment-based configuration loader
├── db.py                      # MySQL connection pool & context manager
├── blueprints/                # HTTP Route Controllers
│   ├── accounts.py            # Account CRUD & balance operations
│   ├── auth.py                # Auth, registration, OAuth & password resets
│   ├── budgets.py             # Category budget management & alerts
│   ├── expenses.py            # Expenses, income & CSV export/import
│   ├── goals.py               # Savings goals & contributions
│   ├── recurring.py           # Recurring subscriptions & payment confirmations
│   ├── reports.py             # Monthly reports & PDF export endpoints
│   └── settlements.py         # Peer balances & settlement accounting
├── services/                  # Business Logic Layer
│   ├── account_service.py     # Atomic account balance calculations
│   ├── budget_service.py      # Budget alert evaluations
│   ├── csv_import_service.py  # CSV parsing, validation & deduplication
│   ├── email_service.py      # SMTP password reset delivery
│   ├── goal_service.py       # Goal percentage & status logic
│   ├── ledger_service.py     # Default categories & transaction summary
│   ├── notification_service.py# In-app notification feed management
│   ├── recurring_service.py   # Auto-charge cron processing & idempotency
│   └── settlement_service.py # Peer balance aggregation
├── templates/                 # Jinja2 HTML Templates
│   ├── accounts/              # Accounts views & modals
│   ├── balances/              # Peer settlements & ledger view
│   ├── expenses/              # Expense table, CSV preview & income drawer
│   ├── goals/                 # Savings goals list & detail views
│   ├── partials/              # Sidebar & mobile topbar includes
│   ├── recurring/             # Recurring subscriptions view
│   └── error.html             # Custom Finora error page
├── database/                  # Schema SQL dumps
├── tests/                     # Automated Test Suite (72 tests)
└── .github/
    └── workflows/
        └── ci.yml             # GitHub Actions CI pipeline
```

---

## Security

- **CSRF Protection**: All HTML forms and AJAX requests carry `csrf_token` validation via Flask-WTF.
- **POST-Only State Mutation**: All toggle, update, delete, close, and settle actions enforce unsafe HTTP methods (`POST`).
- **Data Scoping & Isolation**: Every SQL query is strictly parameter-bound and scoped to `session['user_id']`.
- **Password Security**: Password hashes generated using `werkzeug.security` (bcrypt/pbkdf2). Legacy Google auth passwords automatically migrated to secure random hashes.
- **Secure Tokens**: Password reset tokens created with `secrets.token_urlsafe(32)` and stored as SHA-256 hashes with 1-hour expiration.
- **Environment Isolation**: Sensitive configuration (`SECRET_KEY`, database credentials, SMTP passwords) loaded from `.env` (git-ignored).

---

## Local Setup

### Prerequisites
- Python 3.11+
- MySQL Server 8.0+
- Git

### Steps

1. **Clone the Repository**
   ```bash
   git clone https://github.com/NithyaLakshmi2525/finora.git
   cd finora
   ```

2. **Create & Activate Virtual Environment**
   ```bash
   python -m venv venv
   # Windows PowerShell
   venv\Scripts\Activate.ps1
   # Linux/macOS
   source venv/bin/activate
   ```

3. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Environment Variables**
   Create a `.env` file in the root directory (refer to `config.py`):
   ```env
   FLASK_ENV=development
   SECRET_KEY=your-super-secret-key-change-this
   DB_HOST=127.0.0.1
   DB_USER=root
   DB_PASSWORD=your_mysql_password
   DB_NAME=expense_tracker
   DB_PORT=3306

   # Optional Google OAuth
   GOOGLE_CLIENT_ID=your-google-client-id
   GOOGLE_CLIENT_SECRET=your-google-client-secret

   # Optional Gmail SMTP Password Reset
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USE_TLS=True
   MAIL_USERNAME=your-email@gmail.com
   MAIL_PASSWORD=your-app-password
   ```

5. **Initialize Database**
   Ensure MySQL server is running and create the database:
   ```sql
   CREATE DATABASE expense_tracker CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   ```
   Finora automatically checks and initializes table schemas on application startup (`ensure_schema()`).

6. **Run Application**
   ```bash
   python app.py
   ```
   Access Finora in your browser at `http://127.0.0.1:5000`.

---

## Running Tests

Run the complete automated test suite using `pytest`:

```bash
venv\Scripts\python.exe -m pytest
```

To run with coverage reporting:

```bash
venv\Scripts\python.exe -m pytest --cov=. --cov-report=term-missing
```

---

## CSV Import Flow

1. Navigate to `/import-csv`.
2. Upload a CSV file containing transactions.
3. Review the **Import Preview** screen (`/import-csv/preview`):
   - **Validation**: Inspect valid vs invalid rows.
   - **Duplicate Detection**: Existing transactions matching date, title, and amount are flagged.
   - **Selection**: Check or uncheck individual rows to import.
   - **Target Account**: Map imported rows to a specific deposit/checking account.
4. Click **Import Selected Transactions**. Row IDs shown during preview are informational and do not overwrite database primary keys.

---

## Screenshots

> *Placeholder: Screenshots can be captured and added to docs/screenshots/*

- **Dashboard**: Overview of net worth, recent expenses, budget alerts, and financial metrics.
- **Expenses**: Expense table with slide-over drawer panel, category pills, and filter bar.
- **Income**: Income sources breakdown and slide-over edit drawer.
- **Accounts**: Multi-account balances, account archiving, and creation modal.
- **Budgets**: Per-category spending limits and warning alerts.
- **Goals**: Savings goals progress cards, contribution logs, and goal details.
- **Balances & Settlements**: Peer balance breakdown, payables, receivables, and settlement modal.
- **CSV Import Preview**: CSV row validation, duplicate detection, and import controls.

---

## CI / CD Pipeline

Automated testing is configured via **GitHub Actions** in `.github/workflows/ci.yml`.

- **Triggers**: Pushes and Pull Requests to the `master` branch.
- **Environment**: Ubuntu runner with Python 3.11 and a MySQL 8.0 service container.
- **Execution**: Installs dependencies and runs `pytest` against a clean database instance.

---

## Limitations & Future Improvements

- **Email Delivery**: SMTP email sending runs synchronously in request threads (could be offloaded to a background task queue in high-volume production setups).
- **Multi-Currency Conversion**: Accounts support currency labels (INR, USD, EUR), but live exchange rate conversion is currently static.
- **Database Migrations**: Schema updates run dynamically via `ensure_schema()` at startup; future scaling could adopt Alembic migrations.

---

## License

This project is licensed under the MIT License.
