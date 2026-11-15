# ARIEL HRMS

Ariel HRMS is a fork of the open-source [Horilla HRMS](https://github.com/horilla-opensource/horilla)
(Django). Everything Horilla provides (employees, attendance, leave, payroll, performance, assets,
helpdesk and so on) is still here. This fork **extends the Recruitment module** so that hiring runs
largely on its own:

| # | What we added | One-line summary |
|---|---|---|
| 1 | **Email integration** (Outlook and Gmail) | Connect a mailbox, read candidate applications from it, and send HR mail through it |
| 2 | **Automatic candidate intake from email** | Applications that arrive by email become candidates without anyone typing them in |
| 3 | **Interviews with Microsoft Teams** (and Google Meet), online and offline | Scheduling an interview creates the calendar event and meeting link |
| 4 | **Auto job scoring** | Every new candidate with a resume is scored 0-100 against the job they applied for, with reasons |
| 5 | **Better-job match recommender** | The system tells you when a candidate fits another open job better, and lets you move them in one click |

This README covers only these additions. For the stock Horilla modules, see the upstream project.

```
 Candidate email ──► Outlook / Gmail mailbox ──► Intake poller (every 5 min)
                                                      │  reads PDF resume, asks the LLM to
                                                      │  extract details and pick the open job
                                                      ▼
 Web form / manual add ─────────────────────►  Candidate created
                                                      │  (resume attached)
                                                      ▼
                                   Auto job scoring  (0-100, reasons, confidence)
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

## 1. Email integration (Outlook and Gmail)

Two small Django apps connect a company's mailbox using OAuth:

- **`outlook_auth`**: Microsoft Graph. Stores the Azure app's client ID and tenant, signs in with
  OAuth, keeps the token fresh with a background refresher, and flags the account as
  `reauth_required` (with the last error) when Microsoft asks for a new sign-in.
- **`gmail_auth`**: Google OAuth (Gmail API) with the same token-refresh scheduler.

What the connected mailbox is used for:

- **Reading applications.** See section 2.
- **Sending HR mail.** In the mail server settings each company can choose `send_via`:
  `SMTP` or `Outlook (Connected Account)`. With Outlook selected, outgoing mail goes through Graph
  `sendMail` (`outlook_auth/mailer.py`, used by `base.backends.ConfiguredEmailBackend`), so no SMTP
  password is needed.
- **Calendar events and Teams links.** See section 3.
- **Built-in candidate mails**, with no template or automation to set up:
  - *Application received* with the candidate's portal login (`recruitment/portal_mail.py`),
    controlled by Settings > Recruitment > Candidate Self Tracking.
  - *Application status, not selected* (`recruitment/rejection_mail.py`), with different wording
    for someone rejected after an interview and someone rejected at application stage.
  - If an older, hand-made mail automation already sends the same mail, it keeps sending and the
    built-in one stays quiet, so a candidate never gets two.

## 2. Automatic candidate intake from email

`recruitment/intake.py` turns application emails into `Candidate` records.

1. **Connect a mailbox** under Recruitment settings (`CandidateIntakeMailbox`): choose Outlook or
   Gmail, set a *fallback recruitment* (used when no job matches), and an optional *daily candidate cap*.
2. **A scheduler polls every 5 minutes** (APScheduler, `recruitment/scheduler.py`). A "Poll now"
   button and a live status indicator are available too.
3. **Provider adapters** (`recruitment/intake_providers/outlook.py`, `gmail.py`) convert each
   provider's message format into one shape (`IntakeMessage`), so the pipeline never needs to know
   which provider it is reading.
4. **Resume handling:** the first PDF attachment is used. Oversized attachments (default 15 MB) are
   rejected *before* download, and non-PDF files are logged, not guessed at.
5. **The LLM reads the email and resume text** and returns the candidate's name, email, mobile,
   the best-matching open job and a confidence value.
6. **The candidate is created** in that job's *Applied* stage (or its first stage), duplicates are
   detected, and the poll is recorded in the intake log.
7. **Every email is logged** (`CandidateIntakeLog`) with a status: created, duplicate, no resume,
   invalid format, attachment too large, no job match, parse failed, daily cap exceeded, or error.
   Temporary failures (LLM timeout, daily cap) are retried on the next poll, and any entry can be retried by hand.
   Admins are notified when a mailbox hits its daily cap or starts failing to poll (once, not every 5 minutes).

Safety and scale details: a **database-level lock** (`IntakePollLock`) ensures only one poll runs
even with several web workers; each poll has a message limit (200) and a time budget (20 min); the
polling cursor is not advanced past messages that were not processed.

## 3. Interviews with Microsoft Teams (and Google Meet), online and offline

`InterviewSchedule` was extended and connected to two meeting providers.

- **Interview mode:** `Online` or `Offline`.
  - *Online* interviews get a video meeting link.
  - *Offline* interviews are **still added to the calendar and notified by mail**, just without a link.
- **Optional end time.** If an end time is set, the meeting length is start to end; otherwise it
  defaults to 60 minutes.
- **Provider per company.** Settings > Recruitment > *Default meeting provider* chooses
  **Microsoft Teams** or **Google Meet**. Code lives in `outlook_auth` (Teams, via Graph
  `isOnlineMeeting` with the `teamsForBusiness` provider) and `horilla_meet` (Google Calendar/Meet).
- **Automatic.** The event is created when an interview is scheduled; the "Send Meeting Link"
  button can also create or refresh it. Attendees are the interviewers plus the candidate, the Teams
  join URL and event ID are saved, and the invite body is a formatted table of the interview details.
- **Reschedules update the same event.** **Deleting an interview first marks the invite
  "cancelled" and then removes it**, so attendees are told.
- **Scheduling never fails because of the meeting.** If the mailbox is not connected or the provider
  errors, the interview is still saved and the user is told what to fix or to re-authenticate.

## 4. Auto job scoring

`recruitment/ai_scoring.py` gives every candidate an ATS-style score.

- **Runs automatically** when a candidate with a resume is created, **by any route** (web form,
  manual add, email intake), in a background thread after the database commit, so saving is never slowed down.
- **Output** (`CandidateAIScore`, one row per attempt, history kept): a 0-100 score, key points, the
  reasoning, the model used, a **confidence** (high / low) and flags. Failed attempts are recorded
  with the reason (no resume, unreadable, error).
- **Guards against unreliable scores:**
  - Resumes with under 40 extracted words (scanned or garbled) are not scored.
  - **Two independent scoring passes** are averaged. If they differ by more than 20 points the result
    is marked *low confidence* (`score_inconsistent`).
  - "Missing skills" claimed by the model are **checked against the resume text**. If the model
    says a skill is missing when it is present, the result is flagged `hallucinated_missing_skill`
    and downgraded.
  - If the candidate's **name is not found in the resume** (wrong file attached), the score is
    **capped at 50**, a warning is shown first, and the result is flagged.
- **Where you see it:** the candidate grid and pipeline cards, an *AI Score* tab with a manual
  *re-score* button, and an *AI Effectiveness* panel on the recruitment dashboard (average score,
  email-intake and job-match counts).
- The latest score is stored on the candidate (`latest_ai_score`), so lists and cards do not need to query score history.

## 5. Better-job match recommender

`recruitment/ai_job_match.py` answers "is this person a better fit for a *different* open job?".

- Runs right **after scoring**, so it can compare with the fresh score.
- Compares the resume with the company's **other open jobs** (up to 15 per check).
- A job is only **suggested** when it clearly wins: it must beat the current job's score by **at
  least 15 points**, or reach **70+** if the candidate has no score yet. A small gap is treated as model noise.
- The result is saved (`CandidateJobSuggestion`: suggested job, score, reasoning, history kept) and a
  **badge appears on the candidate's pipeline card**.
- **One click to act:** the *AI Score* tab has a *Move to suggested job* button that opens the normal
  pipeline move dialog. *Check for a better fit* re-runs it manually.

---

## AI provider

All AI features share one client (`recruitment/ai_client.py`). Choose the provider in
**Settings > Integrations > AI Settings**:

- **Ollama (local, default):** `OLLAMA_BASE_URL`, `OLLAMA_INTAKE_MODEL` (default `llama3.2:3b`), `OLLAMA_TIMEOUT`.
- **OpenAI:** API key (stored encrypted) and model (default `gpt-4o-mini`).

Related AI helpers that use the same client: resume-to-application-form autofill, job description
drafting from a title, LinkedIn post drafting, and a plain-language narrative of a candidate's history.

| Setting (environment variable) | Meaning |
|---|---|
| `INTAKE_MAX_ATTACHMENT_BYTES` | Largest resume attachment accepted (default 15 MB) |
| `INTAKE_MAX_MESSAGES_PER_POLL` | Emails handled per poll (default 200) |
| `INTAKE_POLL_MAX_DURATION_SECONDS` | Time budget per poll (default 1200) |
| `INTAKE_DAILY_CANDIDATE_CAP` | Default daily auto-created candidate limit (per-mailbox override available) |

> The Oracle deployment below does **not** run Ollama. Either switch AI Settings to OpenAI, or
> point `OLLAMA_BASE_URL` at an Ollama server. Without an AI provider, candidates are still created
> and meetings still work, but scoring, matching and email-intake parsing will log errors.

## Where the code is

| Area | Location |
|---|---|
| Intake pipeline and providers | `recruitment/intake.py`, `recruitment/intake_providers/` |
| Scoring / job match / AI client | `recruitment/ai_scoring.py`, `ai_job_match.py`, `ai_client.py` |
| Auto-run on new candidate | `recruitment/signals.py` (`auto_score_new_candidate`) |
| Polling schedule | `recruitment/scheduler.py` |
| Interview scheduling and meeting dispatch | `recruitment/views/views.py` (`generate_interview_meeting`) |
| Teams / Outlook | `outlook_auth/` (`methods.py`, `mailer.py`, `signals.py`) |
| Gmail | `gmail_auth/` |
| Google Meet | `horilla_meet/` |
| Candidate mails | `recruitment/portal_mail.py`, `recruitment/rejection_mail.py` |
| Tests | `recruitment/tests/` (intake, providers, AI scoring, interviews, mails, access control) |

## Run and deploy

- **Local / Docker:** see `docker/README.md` and `docker-compose.yml`.
- **Free production hosting on Oracle Cloud (Always Free):** see [`docs/ORACLE_DEPLOY.md`](docs/ORACLE_DEPLOY.md).
  The stack is Django (ASGI, for the candidate chat WebSocket), Postgres, Redis, nginx and Caddy with automatic HTTPS.

## Credits

Built on [Horilla HRMS](https://github.com/horilla-opensource/horilla). See the upstream project for
its license terms, which apply to the original code in this repository.
