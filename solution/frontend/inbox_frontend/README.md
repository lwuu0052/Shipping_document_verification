# Gmail-inspired HarborCheck dashboard

This frontend uses familiar email patterns: a folder sidebar, search, sender previews, stars, category labels, selection, pagination and a message reading pane. It displays the existing HarborCheck emails and reports; it is not connected to a Gmail account.

## Run

From the `shipping-verifier` project folder:

```powershell
.\.venv\Scripts\python.exe gmail_dashboard.py
```

Open http://localhost:8001.

The launcher uses the same backend, `.env`, dataset and SQLite reports as the original app. Use only one dashboard server for processing and review at a time because their background-job locks are process-local. Stop the other server in its terminal with Ctrl+C before starting processing here.

## Use

- Search by email ID, subject, sender or the displayed message preview.
- Choose Inbox, Starred, Needs review, Discrepancies, Verified matches, or an email category.
- Stars are browser-local bookmarks; they do not modify source emails.
- Select messages with checkboxes and choose Verify selected, or use Verify documents for the full dataset.
- Open an email to inspect its message, attachments, seven-field comparison, source evidence and review history.
- Open Review and correct this result to submit verified corrections.
- Open the gear icon to choose processing mode and enter your admin token.
- Export results downloads the organizer-format JSON through the existing exporter.
- On narrow screens, use the top-left menu button to show the sidebar.

No message dates are invented: the provided dataset does not supply a timestamp field, so rows display email IDs. The matched count includes only BL comparison emails with OK reports; other successfully classified messages do not count as verified shipments. Offline/cloud behavior stays explicit.

## Files

| File | Purpose |
|---|---|
| `gmail_dashboard.py` | Serves the fresh frontend and connects it to the existing API |
| `inbox_frontend/index.html` | Dashboard structure |
| `inbox_frontend/inbox.css` | Responsive Gmail-inspired styling |
| `inbox_frontend/inbox.js` | Search, folders, stars, selection, reading pane, review and export |

The CSS optionally loads DM Sans and Manrope from Google Fonts; system fonts provide a fallback. The existing frontend files were not edited because Windows security blocked access to them. No security settings were changed.

The update ZIP contains only these new frontend files and launcher. Extract it into the existing shipping-verifier folder; it requires the project's backend and data.
