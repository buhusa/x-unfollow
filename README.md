# X-Unfollow

A low-cost, review-first terminal app for finding and unfollowing inactive X
accounts through the official X API.

![X-Unfollow terminal demo](docs/assets/demo.gif)

_The walkthrough uses local sample accounts. It makes no X API requests and
changes no real account._

## Why X-Unfollow

- One simple activity scan with no paid Post reads
- Adjustable inactivity threshold, batch size, budget, and safety limits
- Keyboard-controlled terminal interface
- Review and dry-run preview before any unfollow
- Resumable batches instead of scanning the same accounts again
- CSV exports for manual verification
- No scraping, browser automation, or LLM decisions

Any X activity counts: original posts, replies, reposts, and quotes. X-Unfollow
reads each Following resource's latest Post ID and decodes its timestamp
locally. Missing or invalid evidence is always kept, never marked inactive.

## Requirements

- macOS or Linux
- Python 3.11+
- An X Developer App with API credits

X Premium and `console.x.ai` credits do not include X API access.

## Quick start

```bash
git clone https://github.com/buhusa/x-unfollow.git
cd x-unfollow
./start.sh
```

The launcher creates a local Python environment, installs the app, and opens
the menu.

## X Developer setup

1. Open the [X Developer Console](https://console.x.com/) and create an App.
2. Enable **OAuth 2.0** user authentication.
3. Select **Native App** and **Read and write** permissions.
4. Add this callback URL exactly:

   ```text
   http://127.0.0.1:8765/callback
   ```

5. Copy the OAuth 2.0 **Client ID** from **Keys and tokens**.
6. Start X-Unfollow and paste the Client ID when prompted.

The browser handles authorization. Do not paste a Client Secret, access token,
or refresh token into the app.

## Add API credits

In [console.x.com](https://console.x.com/), open your Developer account's
billing area, add a payment method, and purchase X API credits. The app shows
an estimate before every paid scan and blocks scans above your local budget.

At the currently documented rates, scanning 1,000 accounts costs about `$1`:

```text
Authenticated User read       $0.010
1,000 owned Following reads   $1.000
Post reads                     $0.000
------------------------------------
Estimated total               $1.010
```

Pricing can change. Check the official [X API pricing page](https://docs.x.com/x-api/getting-started/pricing)
and Developer Console before larger runs.

## Workflow

```text
1  Scan followed accounts
2  Review and mark candidates
3  Preview marked unfollows
4  Execute marked unfollows
```

Use Up/Down or `j`/`k` to navigate, Enter to select, and `q` to quit. Press `r`
to refresh the current following count. Opening the app and navigating the menu
use only cached local data and make no API calls.

During review, `u` marks an account for unfollow, `k` keeps it, `s` skips it,
and `o` shows its X profile. Marking is local; option `4` still requires a
separate confirmation.

## Batches and exports

Each run continues from X's saved pagination cursor. For example, batches of
100 process accounts 1-100, then 101-200, until the pass is complete.

Option `8` lists:

- `scan_results.csv` - every account in the current pass
- `scan_history.csv` - append-only results from all completed batches
- `candidates.csv` - accounts matching the inactivity rule

## Privacy and safety

Private state remains in the Git-ignored `user-data/` folder, including OAuth
tokens, scan results, exports, and the unfollow audit. Never publish this
folder.

Real unfollows require fresh, complete scan evidence and explicit review. A
configurable per-run cap prevents unexpectedly large operations.

## Direct commands

```bash
.venv/bin/x-unfollow status
.venv/bin/x-unfollow check
.venv/bin/x-unfollow refresh-count
.venv/bin/x-unfollow scan --limit 100
.venv/bin/x-unfollow scan --restart
.venv/bin/x-unfollow review
.venv/bin/x-unfollow unfollow --dry-run
```

For troubleshooting and detailed behavior, see [docs/HOW_TO.md](docs/HOW_TO.md).

## About

Created by [@buhusa](https://x.com/buhusa) | [buhussy.xyz](https://buhussy.xyz/)

## License

[MIT](LICENSE)
