# Upload to GitHub + enable the 3×/day bypass workflow

This gets your project onto GitHub and turns on the scheduled job that runs
`bypass-2.py` three times a day and commits the fresh CSVs back.

**Schedule (edit anytime in `.github/workflows/bypass_schedule.yml`):**
- 08:00 IST (02:30 UTC)
- 15:00 IST (09:30 UTC) ← your requested 3 PM slot
- 22:00 IST (16:30 UTC)

---

## What's in the bundle

```
imperiumai_dashboard/
├── dashboard-3.py                     # Streamlit app (already deployed)
├── gsc_fetch.py                       # Google Search Console live fetcher
├── sites_config.py                    # pages: /view-post/*, /my-profile/*
├── bypass-2.py                        # bot click tracker (headless-aware)
├── requirements.txt
├── .gitignore                         # blocks secrets; ALLOWS report CSVs
├── .streamlit/secrets.toml.template   # GSC key template (fill in Streamlit)
├── .github/workflows/bypass_schedule.yml   # the 3×/day job
├── DEPLOY_STREAMLIT.md
├── GSC_SETUP.md
└── GITHUB_UPLOAD.md                   # this file
```

---

## Option 1 — Upload via the GitHub website (no command line)

1. Unzip `imperiumai_dashboard.zip` on your computer.
2. Go to https://github.com/new → create a repo (private is fine). **Don't**
   tick "Add a README" (keep it empty).
3. On the new repo page, click **uploading an existing file**.
4. Drag in **all** files AND folders from the unzipped folder — including the
   hidden `.github`, `.streamlit`, and `.gitignore`.
   - Finder (Mac): press **⌘ + Shift + .** to show hidden files first.
   - Windows Explorer: **View → Show → Hidden items**.
5. Commit. Done.

> If drag-and-drop won't include `.github/workflows/bypass_schedule.yml`, use
> **Add file → Create new file**, type the path
> `.github/workflows/bypass_schedule.yml` in the name box (the slashes create
> the folders), paste the file's contents, and commit.

## Option 2 — Upload via git (command line)

From inside the unzipped folder:

```bash
git init
git add .
git commit -m "imperiumai.ai SEO dashboard + 3x/day bypass workflow"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

---

## Turn on the scheduled workflow

1. In the repo, open the **Actions** tab. If prompted, click **"I understand
   my workflows, enable them"**.
2. You'll see **"Run bypass 3x daily"** listed. GitHub runs it automatically on
   the schedule above.
3. **Test it now:** open that workflow → **Run workflow** (from the
   `workflow_dispatch` button) → watch it run. When it finishes, check
   `seo_reports/imperiumai_ai/` in the repo for a new
   `traffic_generated_YYYY-MM-DD.csv`.

The workflow already has permission to push commits (`permissions: contents:
write` in the YAML) — no extra setup needed for a normal repo.

---

## How the dashboard sees the new data

The scheduled job commits CSVs to the repo. Your Streamlit Cloud app is
connected to the same repo, so:
- Streamlit Cloud auto-redeploys when the repo changes, and/or
- click **🔄 Refresh data** in the dashboard's Bot view to reload the CSVs.

So: bot job runs → commits CSV → dashboard shows it. Fully automatic.

---

## Changing the run times

Edit the three `cron:` lines in
`.github/workflows/bypass_schedule.yml`. Cron is **UTC**, format
`minute hour * * *`. To convert an IST time to UTC, subtract 5h30m.

Examples:
- 09:00 IST → 03:30 UTC → `30 3 * * *`
- 18:30 IST → 13:00 UTC → `0 13 * * *`

Commit the change and the new schedule takes effect.

---

## Notes / gotchas

- **Filename:** the workflow calls `python bypass-2.py`. If you rename the
  script to `bypass.py`, update that line in the YAML too.
- **Bot clicks from GitHub IPs:** Google/Bing/Yahoo may show CAPTCHAs to
  datacenter IPs more often than your home IP, so click counts from Actions can
  be lower (the script records 0 gracefully rather than failing). If you want
  higher-fidelity bot data, run `bypass-2.py` locally (it runs headful there)
  and push the CSVs, or rely on the **GSC (live)** source for real numbers.
- **GSC needs no schedule:** the Search Console source is fetched live by the
  dashboard, so it's always current without any workflow.
- **Secrets stay out of git:** `.gitignore` blocks the service-account JSON,
  tokens, and `.streamlit/secrets.toml`. Your GSC key lives only in Streamlit
  Cloud's Secrets box.
