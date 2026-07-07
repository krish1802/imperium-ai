# Deploy to Streamlit Community Cloud

This gets your imperiumai.ai SEO dashboard live on a public `*.streamlit.app`
URL, for free — with your Google Search Console key stored **encrypted in
Streamlit Secrets**, never committed to GitHub.

Total time: ~5–10 minutes.

---

## Files in this bundle

| File | Purpose |
|------|---------|
| `dashboard-3.py` | The Streamlit app (main entry point). |
| `gsc_fetch.py` | Live Google Search Console fetcher (reads creds from `st.secrets`). |
| `sites_config.py` | Site + page (`/view-post/*`, `/my-profile/*`) config and URL bucketing. |
| `bypass-2.py` | The bot click tracker (writes the CSVs the bot view reads). |
| `requirements.txt` | Python dependencies Streamlit Cloud installs. |
| `.streamlit/secrets.toml.template` | Template for your GSC key. **Fill the real one into Streamlit, not the repo.** |
| `.gitignore` | Keeps secrets, tokens, and reports out of git. |

> The bot-click view needs CSVs under `seo_reports/imperiumai_ai/`. Those are
> git-ignored (they're generated). On a fresh Cloud deploy the **GSC (live)**
> source works immediately; the **Bot** source shows an empty-state until you
> commit some report CSVs or run `bypass-2.py` and push them.

---

## Step 1 — Get your Google service-account key (one time)

1. [Google Cloud Console](https://console.cloud.google.com/) → create/pick a project.
2. **APIs & Services → Library →** enable **Google Search Console API**.
3. **APIs & Services → Credentials → Create credentials → Service account.**
4. Open the service account → **Keys → Add key → JSON** → download the file.
5. Copy the service-account email (e.g.
   `gsc-dashboard@your-project.iam.gserviceaccount.com`).
6. In **[Search Console](https://search.google.com/search-console) → Settings →
   Users and permissions → Add user**, add that email with **Full** (or
   Restricted) access to the `https://imperiumai.ai/` property.

## Step 2 — Put the code on GitHub

1. Create a new GitHub repo (private is fine).
2. Add all files from this bundle. **Do not add the JSON key** — `.gitignore`
   already blocks `*service_account*.json` and `.streamlit/secrets.toml`.
3. Push.

```bash
git init
git add .
git commit -m "imperiumai.ai SEO dashboard (bot + GSC)"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

## Step 3 — Deploy on Streamlit Community Cloud

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub.**
3. Select your repo/branch, and set **Main file path** to `dashboard-3.py`.
4. Click **Deploy**. It installs `requirements.txt` and boots the app.

## Step 4 — Add your GSC key to Secrets (the secure bit)

1. In the deployed app, open **⋮ → Settings → Secrets** (or **Advanced
   settings → Secrets** during deploy).
2. Open `.streamlit/secrets.toml.template`, copy its contents, and fill every
   value from your downloaded JSON key. **Keep `private_key` in triple quotes
   with its real newlines**, exactly as the template shows.
3. Paste the filled TOML into the Secrets box and **Save**. The app reboots and
   the **Google Search Console (live)** source starts returning real data.

That's it — your dashboard is live.

---

## Local development (optional)

```bash
pip install -r requirements.txt

# Either put the filled secrets at .streamlit/secrets.toml, or use a file:
export GSC_SERVICE_ACCOUNT_FILE=/path/to/service_account.json

streamlit run dashboard-3.py
```

Credential precedence in `gsc_fetch.py`:
1. `st.secrets['gcp_service_account']` (Streamlit Cloud)
2. `GSC_SERVICE_ACCOUNT_FILE` (local file)
3. `GSC_OAUTH_CLIENT_FILE` (local interactive OAuth)

---

## Troubleshooting

- **"No Google credentials configured"** → Secrets not saved, or the
  `[gcp_service_account]` table name/fields don't match the template.
- **403 / "User does not have sufficient permission"** → the service-account
  email wasn't added to the `https://imperiumai.ai/` property in Search Console
  (Step 1.6).
- **`private_key` errors** → it must be a triple-quoted TOML string with real
  line breaks; don't collapse it to one line or escape the newlines.
- **Bot view empty** → expected on a fresh deploy; commit CSVs under
  `seo_reports/imperiumai_ai/` or run `bypass-2.py` and push them.
- **Property mismatch** → `SITE_URL` in `gsc_fetch.py` is
  `https://imperiumai.ai/` (URL-prefix). If you switch to a Domain property,
  change it to `sc-domain:imperiumai.ai`.
