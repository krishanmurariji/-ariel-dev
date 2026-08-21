# Setting Up Automatic Job Posting to LinkedIn

**What this does:** whenever a new job opening is created in the system, it can
automatically post to LinkedIn instead of someone copying and pasting it there
by hand.

**Important to know first:** the job post will appear as coming from
**one specific person's own LinkedIn profile** — not from the "Ariel Software
Solutions" company page directly. LinkedIn doesn't easily allow posting
directly as a company page; that needs special permission from LinkedIn that
takes longer to get. So pick one person (ideally someone in HR/recruiting) who
is okay with job posts showing up under their own name, and use their LinkedIn
account for this setup.

---

## Step 1 — Register the company with LinkedIn (one-time, ~5 minutes)

*This assumes Ariel Software Solutions already has a LinkedIn Company Page.
If it doesn't yet, create one first at linkedin.com/company/setup/new —
takes a couple of minutes, just needs a company name and logo.*

1. Go to **linkedin.com/developers/apps** and click **Create app**.
2. It will ask you to link it to a LinkedIn company page — choose the real
   "Ariel Software Solutions Pvt Ltd" page.
3. Give it a name (e.g. "Ariel Job Postings"), upload a logo, agree to the
   terms, and click Create.

This is basically LinkedIn's way of saying "this company is allowed to use
our tools" — a one-time setup step.

## Step 2 — Turn on the "posting" permission

On the app page, click the **Products** tab, and turn on both of these:
- **Sign In with LinkedIn using OpenID Connect**
- **Share on LinkedIn**

These usually get approved instantly, no waiting.

## Step 3 — Tell LinkedIn where to send people back

Click the **Auth** tab, find "Authorized redirect URLs," and add:
```
https://arielhr.onrender.com/recruitment/linkedin-callback/
```
This just tells LinkedIn "after someone logs in, send them back to our
website" — like a return address.

## Step 4 — Copy two codes

Still on the **Auth** tab, copy:
- The **Client ID** (visible directly)
- The **Client Secret** (click "Show" first to reveal it)

Think of these as a username and password that let our system and LinkedIn
recognize each other. Keep the secret one private — don't share it over
email or chat.

## Step 5 — Connect it inside the HR system

1. Have the chosen person (from the note above) log into the live HR site.
2. Go to **Settings → Recruitment → LinkedIn Integration Setting**.
3. Click **Create**, and fill in:
   - App Name: anything, e.g. "Ariel Recruitment"
   - Email: must be that person's actual LinkedIn email
   - Client ID and Client Secret: paste the two codes from Step 4
   - Company: select Ariel Software Solutions
   - Save
4. A new **LinkedIn button** will appear on that entry — click it. It sends
   you to LinkedIn, you log in and click Allow, and it brings you back
   automatically — done, no further action needed.

## Step 6 — Try it out

Create (or edit) a job opening, tick the **"Publish in LinkedIn"** box, pick
this account, and Save. Check that person's LinkedIn profile — the job post
should appear there within a few seconds.

## One thing to remember later

Every **~60 days**, LinkedIn requires reconnecting (this is a security rule
on their end, not something we can turn off). If job posts silently stop
appearing on LinkedIn after a couple of months, just repeat Step 5.4 — click
the LinkedIn button again and log in once more.
