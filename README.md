<div align="center">

# Horilla AI Recruitment (Ariel HRMS)

### Open-source HR software (a Horilla HRMS fork) with an **AI-powered recruitment (ATS)** module

Read candidate applications straight from **Gmail / Outlook**, **score every resume with AI**,
spot a **better-fitting job** automatically, and schedule **Microsoft Teams / Google Meet** interviews in one click.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-5.2-092E20?logo=django&logoColor=white)
![Channels](https://img.shields.io/badge/ASGI-Django%20Channels-0C4B33)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![AI](https://img.shields.io/badge/AI-Ollama%20%7C%20OpenAI-412991)
![Hosting](https://img.shields.io/badge/Deploy-Oracle%20Cloud%20Always%20Free-F80000?logo=oracle&logoColor=white)

[Features](#-features) · [How it works](#-how-it-works) · [Quick start](#-quick-start) · [AI setup](#-ai-provider) · [Deploy free](#-deploy-for-free) · [Code map](#-where-the-code-is)

</div>

---

## Why Ariel HRMS?

Hiring is mostly repetitive: opening emails, copying details into a form, reading resumes, deciding
which job a person fits, then booking calendars and video links. **Ariel HRMS automates that part**
and leaves the decisions to people.

It is a fork of the open-source [Horilla HRMS](https://github.com/horilla-opensource/horilla), so you
keep a complete HR system (employees, attendance, leave, payroll, performance, assets, helpdesk), and
this fork **extends the Recruitment module**.

## ✨ Features

| | Feature | What you get |
|---|---|---|
| 📧 | **Gmail & Outlook integration** | Connect a mailbox with OAuth. Read applications from it, and optionally send all HR mail through your Outlook account instead of SMTP |
| 📥 | **Automatic candidate intake from email** | A candidate emails a PDF resume and a record appears in the right job's pipeline, with no typing |
| 🤖 | **AI resume scoring** | Every new candidate with a resume gets a 0-100 score, key points, reasoning and a confidence level |
| 🎯 | **Better-job match recommender** | The system tells you when someone fits another open job better, and moves them in one click |
| 🎥 | **Teams & Google Meet interviews** | Scheduling an interview creates the calendar event and meeting link, and mails the interviewers and the candidate |
| 🏢 | **Online and offline interviews** | Offline interviews are still on the calendar and mailed, just without a video link |
| ✉️ | **Built-in candidate mails** | "Application received" (with portal login) and "Not selected" mails, with no template setup |
| 💬 | **Real-time candidate chat** | WebSocket chat on the candidate portal (ASGI + Django Channels) |

Typical search terms this project covers: open-source HRMS, HRIS, applicant tracking system (ATS),
AI resume screening, resume parser, candidate ranking, Django HR software, Gmail / Outlook recruitment
automation, Microsoft Teams interview scheduling, local LLM (Ollama) recruiting.

## 🔄 How it works

```
 Candidate email ──► Gmail / Outlook mailbox ──► Intake poller (every 5 min)
                                                      │  reads the PDF resume; the AI extracts the
                                                      │  details and picks the matching open job
                                                      ▼
 Web form / manual add ─────────────────────►  Candidate created
                                                      │  (resume attached)
                                                      ▼
                                   AI job scoring  (0-100, reasons, confidence)
                                                      │
                                                      ▼
                                   Better-job match check ─► badge on the pipeline card
                                                      │          "Move to suggested job"
                                                      ▼
                                   Schedule interview (online or offline)
                                                      │
                                                      ▼
                           Teams / Google Meet event + invite mailed to everyone
```

---

## 📧 1. Email integration (Outlook and Gmail)

Two small Django apps connect a company mailbox using OAuth:

- **`outlook_auth`**: Microsoft Graph. Stores the Azure app's client ID and tenant, signs in with
  OAuth, keeps the token fresh with a background refresher, and flags the account as
  `reauth_required` (with the last error) when Microsoft asks for a new sign-in.
- **`gmail_auth`**: Google OAuth (Gmail API) with the same token-refresh scheduler.

What the connected mailbox is used for:

- **Reading applications** (section 2).
- **Sending HR mail.** In the mail server settings each company can choose `send_via`:
  *SMTP* or *Outlook (Connected Account)*. With Outlook selected, outgoing mail goes through Graph
  `sendMail` (`outlook_auth/mailer.py`, used by `base.backends.ConfiguredEmailBackend`), so no SMTP
  password is needed.
- **Calendar events and Teams links** (section 3).
- **Built-in candidate mails**, with no template or automation to set up:
  - *Application received* with the candidate's portal login (`recruitment/portal_mail.py`),
    controlled by Settings > Recruitment > Candidate Self Tracking.
  - *Application status, not selected* (`recruitment/rejection_mail.py`), with different wording for
    someone rejected after an interview and someone rejected at application stage.
  - If an older, hand-made mail automation already sends the same mail, it keeps sending and the
    built-in one stays quiet, so a candidate never gets two.

## 📥 2. Automatic candidate intake from email

`recruitment/intake.py` turns application emails into candidate records.

1. **Connect a mailbox** in Recruitment settings: choose Outlook or Gmail, set a *fallback job* (used
   when nothing matches) and an optional *daily candidate cap*.
2. **A scheduler polls every 5 minutes** (APScheduler). A *Poll now* button and a live status
   indicator are available too.
3. **Provider adapters** (`recruitment/intake_providers/`) convert each provider's message format into
   one shape, so the pipeline never needs to know which provider it is reading.
4. **Resume handling:** the first PDF attachment is used. Oversized attachments (default 15 MB) are
   rejected *before* download, and non-PDF files are logged, not guessed at.
5. **The AI reads the email and resume text** and returns the candidate's name, email, mobile, the
   best-matching open job and a confidence value.
6. **The candidate is created** in that job's *Applied* stage (or its first stage), with duplicate detection.
7. **Every email is logged** with a status: created, duplicate, no resume, invalid format, attachment too
   large, no job match, parse failed, daily cap exceeded, or error. Temporary failures (AI timeout, daily
   cap) are retried on the next poll, and any entry can be retried by hand. Admins are notified when a
   mailbox hits its daily cap or starts failing to poll (once, not every 5 minutes).

Built for production: a **database-level lock** ensures only one poll runs even with several web
workers, each poll has a message limit (200) and a time budget (20 min), and the polling cursor is never
advanced past messages that were not processed.

## 🎥 3. Interviews with Microsoft Teams and Google Meet, online and offline

- **Interview mode:** *Online* or *Offline*. Online interviews get a video link. Offline interviews are
  **still added to the calendar and notified by mail**, without a link.
- **Optional end time.** With an end time the meeting length is start to end, otherwise 60 minutes.
- **Provider per company.** Settings > Recruitment > *Default meeting provider* selects **Microsoft
  Teams** (Graph `isOnlineMeeting`, provider `teamsForBusiness`, in `outlook_auth`) or **Google Meet**
  (`horilla_meet`).
- **Automatic.** The event is created when an interview is scheduled, and *Send Meeting Link* can create
  or refresh it. Attendees are the interviewers plus the candidate, the join URL and event ID are saved,
  and the invite body is a formatted table of the interview details.
- **Reschedules update the same event.** Deleting an interview first marks the invite *cancelled* and
  then removes it, so attendees are told.
- **Scheduling never fails because of the meeting.** If the mailbox is not connected or the provider
  errors, the interview is still saved and the user is told what to fix.

## 🤖 4. AI job scoring

`recruitment/ai_scoring.py` gives every candidate an ATS-style score.

- **Runs automatically** when a candidate with a resume is created, by **any route** (web form, manual
  add, email intake), in a background thread after the database commit, so saving is never slowed down.
- **Output** (one row per attempt, history kept): a 0-100 score, key points, reasoning, the model used, a
  **confidence** level (high / low) and flags. Failed attempts are recorded with the reason.
- **Guards against unreliable scores:**
  - Resumes with under 40 extracted words (scanned or garbled) are not scored.
  - **Two independent scoring passes** are averaged. If they differ by more than 20 points the result is
    marked *low confidence*.
  - "Missing skills" claimed by the model are **checked against the resume text**. If a skill is claimed
    missing but is present, the result is flagged and downgraded.
  - If the candidate's **name is not found in the resume** (wrong file attached), the score is **capped
    at 50**, a warning is shown first, and the result is flagged.
- **Where you see it:** candidate grid and pipeline cards, an *AI Score* tab with a manual *re-score*
  button, and an *AI Effectiveness* panel on the recruitment dashboard (average score, email-intake and
  job-match counts).

## 🎯 5. Better-job match recommender

`recruitment/ai_job_match.py` answers: *is this person a better fit for a different open job?*

- Runs right **after scoring**, so it can compare against the fresh score.
- Compares the resume with the company's **other open jobs** (up to 15 per check).
- A job is only **suggested** when it clearly wins: it must beat the current job's score by **at least 15
  points**, or reach **70+** if the candidate has no score yet. A small gap is treated as model noise.
- The result is saved with its reasoning, and a **badge appears on the candidate's pipeline card**.
- **One click to act:** *Move to suggested job* opens the normal pipeline move dialog. *Check for a better
  fit* re-runs it manually.

---

## 🧠 AI provider

All AI features share one client (`recruitment/ai_client.py`). Choose the provider in
**Settings > Integrations > AI Settings**:

| Provider | Cost | Notes |
|---|---|---|
| **Ollama** (local, default) | Free, runs on your own server | `OLLAMA_BASE_URL`, `OLLAMA_INTAKE_MODEL` (default `llama3.2:3b`), `OLLAMA_TIMEOUT` |
| **OpenAI** | Pay per use | API key (stored encrypted) and model (default `gpt-4o-mini`) |

The same client also powers resume-to-application-form autofill, job description drafting from a title,
LinkedIn post drafting, and a plain-language narrative of a candidate's history.

| Environment variable | Meaning |
|---|---|
| `INTAKE_MAX_ATTACHMENT_BYTES` | Largest resume attachment accepted (default 15 MB) |
| `INTAKE_MAX_MESSAGES_PER_POLL` | Emails handled per poll (default 200) |
| `INTAKE_POLL_MAX_DURATION_SECONDS` | Time budget per poll (default 1200) |
| `INTAKE_DAILY_CANDIDATE_CAP` | Default daily auto-created candidate limit (per-mailbox override available) |

> Without a configured AI provider, candidates are still created and meetings still work, but scoring,
> matching and email-intake parsing will log errors.

## 🚀 Quick start

Requires Docker and Docker Compose.

```bash
git clone https://github.com/krishanmurariji/horilla-ai-recruitment.git
cd horilla-ai-recruitment
make dev            # web + PostgreSQL + Redis, at http://localhost:8000
```

On the first run in development mode, the login page offers **Initialize Database** (create the first
admin and company) and **Load Demo Data**. See [`docker/README.md`](docker/README.md) for details.

## ☁️ Deploy for free

A complete, **$0** production setup on **Oracle Cloud Always Free** (ARM VM) is included: Django on ASGI
(for the chat WebSocket), PostgreSQL, Redis, nginx, and Caddy with automatic HTTPS.
Follow [`docs/ORACLE_DEPLOY.md`](docs/ORACLE_DEPLOY.md).

## 🗺️ Where the code is

| Area | Location |
|---|---|
| Intake pipeline and providers | `recruitment/intake.py`, `recruitment/intake_providers/` |
| Scoring, job match, AI client | `recruitment/ai_scoring.py`, `ai_job_match.py`, `ai_client.py` |
| Auto-run on a new candidate | `recruitment/signals.py` (`auto_score_new_candidate`) |
| Polling schedule | `recruitment/scheduler.py` |
| Interview scheduling and meeting dispatch | `recruitment/views/views.py` (`generate_interview_meeting`) |
| Teams / Outlook | `outlook_auth/` |
| Gmail | `gmail_auth/` |
| Google Meet | `horilla_meet/` |
| Candidate mails | `recruitment/portal_mail.py`, `recruitment/rejection_mail.py` |
| Tests | `recruitment/tests/` (intake, providers, AI scoring, interviews, mails, access control) |

## 🧰 Tech stack

Python 3.12 · Django 5.2 · Django Channels (ASGI, WebSockets) · PostgreSQL · Redis · APScheduler ·
Gunicorn + Uvicorn · Microsoft Graph API · Gmail API · Google Calendar / Meet · Ollama / OpenAI · Docker · Caddy · nginx

## 🙏 Credits

Built on [Horilla HRMS](https://github.com/horilla-opensource/horilla). See the upstream project for its
license terms, which apply to the original code in this repository.

This project is an independent fork and is **not affiliated with or endorsed by** Horilla or its maintainers.
"Horilla" is used only to describe the software this project is based on.

---

<sub>Keywords: HRMS, HRIS, HR software, open source, applicant tracking system, ATS, recruitment software, AI recruiting,
resume screening, resume parser, candidate scoring, job matching, Django, Gmail, Outlook, Microsoft Teams,
Google Meet, interview scheduling, Ollama, OpenAI.</sub>
