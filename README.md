# Naukari Adda

A Django-based campus placement portal that connects students with recruiters.
Students upload a resume, the system reads it (including scanned PDFs via OCR),
scores how well they match each job, gates applications behind a role-based
eligibility quiz with webcam monitoring, and tracks every application through to
selection. Recruiters post jobs, see ranked applicants with skill-match
breakdowns, update statuses and schedule interviews.

---

## Table of contents

- [Purpose](#purpose)
- [Features](#features)
- [Technology stack](#technology-stack)
- [Django architecture](#django-architecture)
- [Database schema](#database-schema)
- [Resume extraction and OCR](#resume-extraction-and-ocr)
- [Resume matching](#resume-matching)
- [Claude AI analysis](#claude-ai-analysis)
- [The quiz](#the-quiz)
- [Webcam monitoring](#webcam-monitoring)
- [Email notifications](#email-notifications)
- [Setup](#setup)
- [Environment variables](#environment-variables)
- [Running the project](#running-the-project)
- [Running the tests](#running-the-tests)
- [Project structure](#project-structure)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)

---

## Purpose

College placement drives usually run on spreadsheets and email threads.
Recruiters get a pile of undifferentiated resumes; students get no feedback on
why they were passed over. Naukari Adda addresses both sides:

- **Students** see a concrete match percentage per job, which of the required
  skills they already have, which they are missing, and what to do about it.
- **Recruiters** get applicants ranked by skill match and quiz score, so the
  strongest candidates surface without manual triage.
- **Administrators** manage users, jobs and the quiz question bank from the
  Django admin.

---

## Features

### Student
- Registration and login with a role chosen at signup
- Resume upload (PDF, PNG, JPG) with file-type and size validation
- Automatic text extraction, with OCR fallback for scanned documents
- Automatic skill detection from resume text
- Candidate profile with a completion percentage and a breakdown of what is
  still missing
- Job board with server-side search and filtering by title, company, role and
  required skill
- AI match percentage, matched skills and missing skills on every job
- Role-based eligibility quiz, one attempt per job, with a per-question timer
- Live webcam preview and basic presence monitoring during the quiz
- Application status tracking from Quiz Pending through to Selected
- In-app notifications and email alerts for status changes and interviews

### Recruiter
- Post jobs with a required-skills list, a quiz role and a minimum score
- Applicant list ranked by match percentage and quiz score
- Per-applicant matched/missing skill breakdown and full AI analysis
- One-click status updates (Shortlisted, Interview Scheduled, Selected,
  Rejected), each of which notifies the student
- Interview scheduling with date, time and meeting link
- Dashboard analytics across all posted jobs

### Administrator
- Full Django admin over users, jobs, applications, notifications and quiz
  questions
- Summary dashboard with user, job and application counts

---

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Django 5.2+ |
| Database | SQLite (development default); any Django-supported DB in production |
| Frontend | Django templates, Bootstrap 5.3, vanilla JavaScript |
| PDF parsing | pdfplumber, PyPDF2 |
| OCR | Tesseract via pytesseract, Poppler via pdf2image, Pillow |
| AI | Anthropic Claude (official Python SDK), optional |
| Webcam | `navigator.mediaDevices.getUserMedia`, Canvas, optional `FaceDetector` |
| Config | python-dotenv |
| Auth | Django's built-in auth over a custom user model |

---

## Django architecture

The project is `naukari_adda` with three apps.

```
naukari_adda/            Project config: settings, root URLconf, WSGI/ASGI
users/                   Authentication, profiles, resume upload
jobs/                    Jobs, applications, matching, AI, notifications
quiz/                    Question bank, attempts, answers, scoring
```

### `users`
Owns `CustomUser` (set as `AUTH_USER_MODEL`, adding a `user_type` of student /
recruiter / admin) and `StudentProfile`, which holds the resume file, the
extracted text, detected skills and the candidate profile fields. Also owns the
auth views and the role-dispatching `dashboard` view.

`users/resume_utils.py` is a standalone module handling all text extraction, so
the view stays thin and the extraction logic is independently testable.

### `jobs`
The largest app. Beyond the `Job`, `Application` and `Notification` models it
contains three support modules:

- **`jobs/matching.py`** — the rule-based matching engine. Dependency-free, so
  it always works. Single source of truth for skill normalisation.
- **`jobs/ai_analysis.py`** — the Claude integration, with automatic fallback to
  `matching.py`.
- **`jobs/notifications.py`** — the only place notifications are created. Always
  writes the in-app row first, then attempts email inside a `try/except`.

### `quiz`
Holds `Question` (tagged by role), `QuizAttempt` (one per student per job) and
`QuizAnswer` (individual responses, kept for review). The quiz is a session-
driven, one-question-per-request flow.

### Request flow, student side

```
register/login
  → upload resume       → pdfplumber / PyPDF2 / OCR → resume_text + skills
  → browse jobs         → server-side filtering + live match calculation
  → view job detail     → Claude analysis (or rule-based fallback)
  → take quiz           → webcam starts, timer runs, answers held in session
  → quiz scored         → Eligible / Not Eligible, Application created
  → apply               → status becomes Applied, recruiter notified
  → recruiter updates   → Notification created + email attempted
```

---

## Database schema

```
CustomUser (AbstractUser)
  username, email, password, user_type

StudentProfile            1:1 → CustomUser
  resume (FileField), resume_text, skills,
  full_name, phone, headline, education, projects, certifications

Job                       FK → CustomUser (posted_by)
  title, company, description, role, required_skills,
  min_score_required, created_at

Application               FK → CustomUser (student), FK → Job
  status, applied_at, status_updated_at,
  interview_date, interview_time, meeting_link
  UNIQUE (student, job)   ← prevents duplicate applications

Notification              FK → CustomUser
  message, is_read, created_at

Question
  role, text, option1..option4, correct_option

QuizAttempt               FK → CustomUser, FK → Job
  score, total, attempted_at

QuizAnswer                FK → CustomUser, FK → Question
  selected_option, is_correct, answered_at
  UNIQUE (user, question)
```

### Application statuses

`Quiz Pending` → `Eligible` / `Not Eligible` → `Applied` → `Shortlisted` →
`Interview Scheduled` → `Selected` / `Rejected`

The first three are set by the system. `Shortlisted`, `Interview Scheduled`,
`Selected` and `Rejected` are the only statuses a recruiter can set by hand, and
the view validates against that list rather than trusting the submitted value.

---

## Resume extraction and OCR

`users/resume_utils.py` tries four strategies in order, stopping at the first
that produces text:

1. **pdfplumber** — fast and accurate on text-based PDFs.
2. **PyPDF2** — rescues PDFs that pdfplumber cannot open.
3. **pdf2image + pytesseract** — renders each page at 200 DPI and OCRs it.
   This is what handles scanned resumes.
4. **Pillow + pytesseract** — direct OCR for uploaded images (JPG/PNG).

Every step is individually wrapped. A missing dependency or a corrupt file logs
a warning and moves to the next strategy; the function returns `''` rather than
raising. An upload with no readable text still succeeds, and the student is told
to add skills manually.

Tesseract and Poppler locations come from `TESSERACT_CMD` and `POPPLER_PATH`, so
the project is not tied to one machine's install paths.

---

## Resume matching

`jobs/matching.py` implements the rule-based matcher.

**Normalisation.** Every skill — from a resume, a job posting or a search box —
passes through `canonicalize()`, which lower-cases, collapses whitespace, strips
decorative punctuation while preserving meaningful characters (`+`, `#`, `.`,
`/`), and maps aliases onto a canonical name:

```
'  python3 '  → 'Python'      'REACT.JS' → 'React'
'power-bi'    → 'Power BI'    'node js'  → 'Node.js'
'postgres'    → 'PostgreSQL'  'c++'      → 'C++'
```

Unknown skills are title-cased rather than discarded, so niche technologies
still compare consistently between a resume and a job.

**Parsing.** `parse_skill_list()` accepts comma, semicolon, pipe or newline
separated input, and splits on `/` only when the whole token is not itself a
recognised skill — so `django/react` becomes two skills but `CI/CD` stays one.

**Extraction.** `extract_skills_from_text()` scans free-form resume text using
custom word boundaries (`\b` does not work next to `+`, `#` or `.`). This is why
`Java` does not match inside `JavaScript`, and why one- and two-character skills
like `R`, `C` and `Go` use a stricter pattern and do not fire on ordinary words.

**Scoring.** A required skill counts as matched if the candidate lists it, if it
was detected in the resume text, or if the exact required phrase appears in the
resume. The percentage is `matched / required × 100` — computed, never
hard-coded. With no required skills listed, the result is flagged
`has_requirements: False` and the UI says so instead of showing a misleading 0%.

---

## Claude AI analysis

Optional. Enabled by setting `ANTHROPIC_API_KEY`.

When available, Claude returns a structured analysis: detected skills, matched
skills, missing skills, strengths, a match percentage, a short explanation and
improvement suggestions. Students see it on the job detail page; recruiters see
it per applicant.

**Privacy.** Only the resume text, job title, role, description and required
skills are sent. Before transmission, `_strip_personal_information()` redacts
email addresses, phone numbers and social profile URLs. No usernames, database
IDs or account details leave the application.

**Reliability.** Claude is never load-bearing. The call falls back to
`jobs/matching.py` when the SDK is missing, the key is absent, the request
fails or times out, or the response cannot be parsed into the expected shape.
Every response is validated and coerced — a percentage outside 0–100 or a
missing explanation triggers the fallback rather than rendering garbage. The UI
labels which engine produced the result and, when relevant, why Claude was not
used.

**Storage.** Results are cached in Django's cache framework keyed by a hash of
the resume and job content, not written to the database. No schema change was
needed, and repeated page loads do not re-bill the API. Cache TTL is 6 hours;
both the student and recruiter views offer a "re-run analysis" link.

---

## The quiz

- Questions are drawn from the bank matching the job's `role`.
- Five questions are selected at random per attempt and their shuffled order is
  stored in the session, so the order the student sees is the order used for
  scoring.
- One question per page, with a 30-second timer that auto-submits on expiry.
- Exactly one attempt per student per job, enforced on entry and re-checked
  before the attempt is written, so a double submit cannot create two rows.
- Submitted answers are validated: the option must be 1–4 and the question must
  belong to the quiz actually served, so a crafted POST cannot inject answers.
- Scoring compares against `correct_option`; the score is checked against the
  job's `min_score_required` to set `Eligible` or `Not Eligible`.
- The question set is bound to a job id, so abandoning one job's quiz cannot
  leak its questions into another job's.
- A role with no questions yet shows a clear message rather than failing.

---

## Webcam monitoring

Implemented in `templates/quiz/quiz.html`.

- Requests permission via `navigator.mediaDevices.getUserMedia({ video: true })`
  when the quiz page loads.
- Shows a live preview alongside the questions, with a status badge reading
  Starting / Active / Unavailable.
- Keeps the stream open for the duration of the quiz and stops all tracks on
  submit, timer expiry, page hide and unload — so the camera light goes out when
  the student is done.
- Distinguishes permission denied, no camera found, camera in use by another
  application and unsupported browser, showing specific guidance for each.
- The quiz remains fully usable if the camera is unavailable.

**Presence check.** Where the browser exposes the Shape Detection API, a
`FaceDetector` samples a downscaled frame every three seconds and reports
"face visible", "no face visible" or the number of people visible. Elsewhere it
falls back to a brightness-and-variance heuristic on a canvas, which catches an
obviously covered or dark camera.

**This is not AI proctoring.** It is a modest presence indicator. Nothing is
recorded, nothing is uploaded, no identity is verified and no result is stored
or acted on. The page says as much to the student.

---

## Email notifications

All notifications go through `jobs/notifications.py`:

```python
notify(user, message, email_subject=None, email_body=None)
```

The in-app `Notification` row is created first and unconditionally. Email is
attempted afterwards inside a `try/except`, so an SMTP outage, a wrong password
or a missing address can never turn into a 500 for the recruiter who triggered
the action. The backward-compatible `notify()` function returns whether email delivery
succeeded. The richer `notify_result()` helper distinguishes successful
delivery, missing/invalid recipient addresses and delivery failures. Both
create the in-app notification first. Each email is addressed to exactly one
student account; a failure for one recipient does not stop later recipients.
Recruiter status updates do not resend the same status notification when the
application is already in that status. Bulk emails support `{name}` and show
which candidates were sent, skipped for missing addresses, or failed.

Configuration is entirely environment-driven. If `EMAIL_HOST_USER` and
`EMAIL_HOST_PASSWORD` are blank, the project automatically selects Django's
console backend — emails print to the terminal, development works, nothing
crashes.

Emails are sent for: application submitted, shortlisted, interview scheduled,
selected and rejected. The recruiter applicants page also supports selecting
multiple applicants and sending each one a separate personalized email. The
bulk message accepts `{name}` as a placeholder for each student's preferred
profile name (then account name, then username). The subject is limited to
200 characters and the message to 5,000 characters.

For real delivery, configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` and `DEFAULT_FROM_EMAIL` in environment
variables (or a local `.env` file that is never committed). If SMTP credentials
are not configured, Django's console backend prints messages in the terminal;
that mode does not deliver real email.

---

## Setup

### Prerequisites
- Python 3.10 or newer
- pip
- Optional, for OCR of scanned resumes: Tesseract and Poppler

### 1. Get the code

```bash
git clone <your-repository-url>
cd naukari_adda
```

### 2. Create a virtual environment

```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install the OCR binaries (optional)

Only needed to read scanned or image-based resumes. Text-based PDFs work
without them.

| | Tesseract | Poppler |
|---|---|---|
| **Windows** | [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) | [poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases) |
| **macOS** | `brew install tesseract` | `brew install poppler` |
| **Ubuntu** | `sudo apt install tesseract-ocr` | `sudo apt install poppler-utils` |

On Windows, set `TESSERACT_CMD` and `POPPLER_PATH` in `.env`. On macOS and
Linux, leave both blank.

### 5. Create your `.env`

```bash
cp .env.example .env
```

Generate a secret key and paste it in:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 6. Set up the database

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 7. Backfill skills (existing databases only)

If you are upgrading an existing database, this re-scans stored resumes and
populates the skills field:

```bash
python manage.py backfill_skills
```

Fresh installs can skip it. Skills are detected automatically on upload.

### 8. Add quiz questions

Log in at `/admin/` and add questions under **Quiz → Questions**. Each question
needs a role (`Python`, `Frontend`, `Data Analyst`, `DevOps`), text, four
options and the correct option number. **Add at least five questions per role**
you intend to use — a job whose role has no questions cannot run its quiz.

---

## Environment variables

Full documentation lives in `.env.example`. Summary:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DJANGO_SECRET_KEY` | Yes in production | insecure dev key | Cryptographic signing |
| `DJANGO_DEBUG` | No | `True` | Debug mode |
| `DJANGO_ALLOWED_HOSTS` | Yes when `DEBUG=False` | localhost | Permitted hosts |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | No | empty | Origins trusted for CSRF |
| `DJANGO_TIME_ZONE` | No | `Asia/Kolkata` | Display timezone |
| `EMAIL_HOST` | No | `smtp.gmail.com` | SMTP server |
| `EMAIL_PORT` | No | `587` | SMTP port |
| `EMAIL_HOST_USER` | No | empty | SMTP username |
| `EMAIL_HOST_PASSWORD` | No | empty | SMTP password / app password |
| `EMAIL_USE_TLS` | No | `True` | STARTTLS |
| `EMAIL_USE_SSL` | No | `False` | Implicit SSL |
| `EMAIL_TIMEOUT` | No | `15` | SMTP timeout in seconds |
| `DEFAULT_FROM_EMAIL` | No | `EMAIL_HOST_USER` | Sender address |
| `ANTHROPIC_API_KEY` | No | empty | Enables Claude analysis |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-5` | Model to call |
| `TESSERACT_CMD` | No | empty | Path to the Tesseract binary |
| `POPPLER_PATH` | No | empty | Path to Poppler's `bin` directory |
| `RESUME_MAX_SIZE_MB` | No | `5` | Upload size limit |

**Gmail specifically:** use a 16-character App Password, not your account
password, and enable 2-Step Verification first. Create one at
<https://myaccount.google.com/apppasswords>.

---

## Running the project

```bash
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

| Path | Purpose |
|---|---|
| `/` | Landing page |
| `/register/` | Create a student or recruiter account |
| `/login/` | Log in |
| `/dashboard/` | Role-aware dashboard |
| `/jobs/` | Job board with search and filters |
| `/jobs/<id>/` | Job detail with AI analysis |
| `/profile/` | Candidate profile |
| `/upload-resume/` | Resume upload |
| `/post-job/` | Post a job (recruiter) |
| `/recruiter/jobs/` | Manage posted jobs (recruiter) |
| `/admin/` | Django admin |

> **Webcam note:** browsers only grant camera access over `http://localhost` or
> HTTPS. Reaching the dev server via a LAN IP such as `http://192.168.1.5:8000`
> will block the camera. The quiz still works; only the preview is unavailable.

---

## Running the tests

```bash
python manage.py test
```

78 tests covering skill normalisation and extraction, match calculation,
resume-upload validation, registration and login, the full quiz flow, scoring
accuracy, one-attempt enforcement, job search and filtering, notification
delivery with simulated email failure, Claude fallback behaviour, and
authorization boundaries between recruiters.

Individual apps:

```bash
python manage.py test jobs
python manage.py test quiz
python manage.py test users
```

Configuration check only:

```bash
python manage.py check
```

---

## Project structure

```
naukari_adda/
├── manage.py
├── requirements.txt
├── .env.example              # template - commit this
├── .env                      # real secrets - NEVER commit
├── .gitignore
├── README.md
├── db.sqlite3                # git-ignored
│
├── naukari_adda/             # project configuration
│   ├── settings.py           # env-driven, no hard-coded secrets
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── users/
│   ├── models.py             # CustomUser, StudentProfile
│   ├── views.py              # auth, dashboard, profile, resume upload
│   ├── forms.py              # registration, resume validation, profile
│   ├── resume_utils.py       # PDF + OCR extraction
│   ├── admin.py
│   ├── urls.py
│   └── tests.py
│
├── jobs/
│   ├── models.py             # Job, Application, Notification
│   ├── views.py              # job board, applicants, status, interviews
│   ├── matching.py           # skill normalisation + matching engine
│   ├── ai_analysis.py        # Claude integration with fallback
│   ├── notifications.py      # in-app + safe email
│   ├── admin.py
│   ├── urls.py
│   ├── tests.py
│   └── management/commands/
│       └── backfill_skills.py
│
├── quiz/
│   ├── models.py             # Question, QuizAttempt, QuizAnswer
│   ├── views.py              # session-driven quiz flow
│   ├── admin.py
│   ├── urls.py
│   └── tests.py
│
├── templates/
│   ├── base.html
│   ├── home.html
│   ├── student_dashboard.html
│   ├── recruiter_dashboard.html
│   ├── admin_dashboard.html
│   ├── users/
│   │   ├── login.html
│   │   ├── register.html
│   │   ├── profile.html
│   │   └── upload_resume.html
│   ├── jobs/
│   │   ├── job_list.html
│   │   ├── job_detail.html
│   │   ├── post_job.html
│   │   ├── recruiter_jobs.html
│   │   ├── view_applicants.html
│   │   ├── applicant_analysis.html
│   │   └── schedule_interview.html
│   └── quiz/
│       ├── quiz.html         # includes the webcam JavaScript
│       └── result.html
│
└── media/resumes/            # uploaded files - git-ignored
```

---

## Security notes

- **No secrets in source.** Every credential comes from the environment.
  `.env` is git-ignored; `.env.example` carries placeholders only.
- **Authentication.** Django's session auth over a custom user model. Every
  non-public view is `@login_required`.
- **Authorization.** Recruiter views verify that the logged-in recruiter
  actually posted the job before showing applicants or allowing status changes,
  returning `403` otherwise. Students cannot reach recruiter views and vice
  versa. Accounts cannot self-register as `admin`.
- **CSRF.** Enabled globally. Every state-changing action is a POST form with
  `{% csrf_token %}` — applying, status updates, select, reject and scheduling.
  This means they cannot be triggered by a crafted link, an image tag or a
  browser prefetch.
- **Duplicate prevention.** `UNIQUE (student, job)` on `Application` blocks
  duplicate applications at the database level; the view also checks status
  before transitioning. `QuizAttempt` is checked on quiz entry and again before
  writing.
- **Input validation.** Uploads are checked for extension, MIME type, size and
  magic bytes, so a renamed executable is rejected. Quiz answers are validated
  for range and question ownership. Recruiter status values are validated
  against an allowlist. The login `next` parameter is restricted to relative
  paths to prevent open redirects.
- **API key protection.** `ANTHROPIC_API_KEY` is read from the environment at
  call time and never logged or rendered.
- **Data minimisation.** Personal identifiers are stripped from resume text
  before it reaches the Claude API.
- **Production hardening.** With `DJANGO_DEBUG=False`, secure cookies, HSTS,
  SSL redirect and proxy SSL headers activate automatically.

### Before deploying

1. Set `DJANGO_DEBUG=False` and populate `DJANGO_ALLOWED_HOSTS`.
2. Generate a fresh `DJANGO_SECRET_KEY`.
3. Serve behind HTTPS.
4. Run `python manage.py collectstatic` and serve static and media files from
   your web server rather than Django.
5. Consider PostgreSQL instead of SQLite.
6. Swap the local-memory cache for Redis or Memcached if running multiple
   workers, so the Claude analysis cache is shared.

---

## Troubleshooting

**AI match shows 0% for everyone.**
Check the job actually has `required_skills` filled in — the applicant page
flags a job with none. On an upgraded database, run
`python manage.py backfill_skills`.

**A student's match is 0% but their resume looks right.**
The resume text may be empty. Scanned PDFs need Tesseract and Poppler installed
and, on Windows, `TESSERACT_CMD` and `POPPLER_PATH` set. Check
`StudentProfile.resume_text` in the admin. The student can always add skills
manually from `/profile/`.

**Emails are not arriving.**
With blank credentials the project uses the console backend by design — check
your terminal. With Gmail, confirm you are using an App Password and not your
account password. Email failures are logged at `ERROR` level and never crash a
request, so check the server log.

**The camera does not start.**
Browsers require `http://localhost` or HTTPS. A LAN IP will be blocked. Also
check no other application is holding the camera, and that permission was not
previously denied for the site.

**"No quiz questions have been added for this role."**
Add at least five questions for that role in the admin under Quiz → Questions.

**Claude analysis always says "skill matching".**
`ANTHROPIC_API_KEY` is not set, the `anthropic` package is missing, or the
request failed. The panel states the specific reason. This is a degraded mode,
not an error — the portal is designed to work without it.

---

## License

Specify a license before publishing this repository.
