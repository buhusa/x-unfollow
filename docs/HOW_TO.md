# X-Unfollow Setup Guide

This is the slightly more detailed companion to the main
[README](../README.md).

## Before you start

You need:

- Python 3.11 or newer;
- an X account;
- an X Developer account;
- an X Developer App; and
- X API credits.

X Premium and xAI credits do not pay for X API requests.

## Configure the X App

1. Open [console.x.com](https://console.x.com/).
2. Create an App or select an existing one.
3. Open its user authentication settings.
4. Enable OAuth 2.0.
5. Select **Native App**.
6. Select **Read and write** permissions.
7. Register this callback exactly:

   ```text
   http://127.0.0.1:8765/callback
   ```

8. Save the settings.
9. Open **Keys and tokens**.
10. Copy the OAuth 2.0 **Client ID**.

The App needs these scopes:

```text
tweet.read users.read follows.read follows.write offline.access
```

`offline.access` allows the app to refresh an expired login without making you
authorize it every time.

Do not paste a Client Secret, app Bearer Token, access token, or refresh token
into X-Unfollow. The terminal asks only for the Client ID.

## Add X API credits

1. Stay in the [X Developer Console](https://console.x.com/).
2. Select your Developer account.
3. Open its billing or credits area.
4. Add a payment method if required.
5. Purchase X API credits.

You can optionally configure:

- auto-recharge;
- a low-balance trigger; and
- a spending limit.

The console displays current endpoint prices and your remaining balance. The
official [pricing page](https://docs.x.com/x-api/getting-started/pricing)
explains the pay-per-use model.

## Install and connect

```bash
git clone https://github.com/buhusa/x-unfollow.git
cd x-unfollow
./start.sh
```

On first launch:

1. Accept the guided setup.
2. Paste the OAuth 2.0 Client ID.
3. Authorize the intended X account in the browser.
4. Wait for the read-only connection test.

Local state is stored in the visible `user-data/` folder. OAuth token files are
created with owner-only permissions on supported systems.

## Recommended first run

1. Open option `5` and review the inactivity thresholds and budget.
2. Use option `1` to run a small scan.
   Repeating option `1` continues with the next batch until the full list is done.
3. Use option `2` to review candidates.
4. Press `u` only for accounts you want to mark.
5. Use option `3` for the dry-run preview.
6. Use option `4` only when the preview is correct.

Marking an account during review does not unfollow it. Option `4` performs the
real action after another confirmation.

## Cost controls

Before a scan, X-Unfollow shows a conservative worst-case estimate.

The scan is blocked when that estimate exceeds the configured hard budget,
even when a command uses `--yes`. You can adjust the budget and maximum account
count in option `5`.

Option `5` also controls the maximum evidence age for real unfollows. The
default is 24 hours. Older marked results remain local but cannot be executed
until a fresh scan confirms them again.

Actual charges can be lower due to fewer returned resources and X API
deduplication. Pricing can change, so check the Developer Console.

### Activity Scan

The scan counts every Post type as activity: original posts, replies, reposts,
and quotes. It reads `most_recent_tweet_id` from each Following resource and
decodes the Snowflake timestamp locally. The app has no Post-lookup or account-
timeline scan path.

At current documented prices, 1,000 accounts are estimated at about `$1.01`:

```text
Authenticated user read             $0.010
1,000 owned Following reads         $1.000
Post reads                           $0.000
-------------------------------------------
Estimated total                     $1.010
```

An account with recent reposts still counts as active. If X provides no valid
latest-activity ID, the result is marked incomplete and kept. The tool does not
guess that missing data means inactivity.

## Continue or restart a scan

X-Unfollow stores X's pagination cursor after a batch completes successfully.
For a limit of 100, repeated scans therefore process 1-100, then 101-200, and
so on. The menu shows the number scanned in the current pass.

The cursor is advanced only after the batch results and exports have been
saved. If a scan fails before that point, it is safe to retry, although X may
charge again for repeated API reads.

After the full list is complete, use `scan --restart` or confirm a new pass in
the menu to begin again. A changed X account or changed scan rules starts or
requires a separate pass so data is not mixed.

The X following list is live rather than a frozen snapshot. Avoid follow and
unfollow changes until a multi-batch pass is complete.

X notes that saved pagination tokens may expire. If that happens, existing
results remain intact and `scan --restart` begins a fresh pass.

Older `0.1.x` scan files have no reusable X pagination cursor. The first scan
after upgrading starts a new pass from the beginning. Every later batch can be
resumed normally.

## Refresh the current following count

Press `r` in the main menu to fetch the account's current `following_count`.
The menu caches the value with its refresh age, so navigating around the app
does not make additional API calls. The count is also refreshed during the
connection test and at the start of a paid scan. One manual refresh currently
costs about `$0.01` before X billing deduplication.

## Check the tool's work

After each successful batch, option `8` lists:

```text
scan_results.csv  all accounts in the current pass
scan_history.csv  append-only results from every completed batch
candidates.csv    only current inactivity candidates
```

`scan_results.csv` is the easiest file for manual verification. It includes
the inactivity threshold, scan time, account status, decision, latest activity
timestamp and age, plus a direct evidence URL. The export deliberately does not
contain OAuth credentials or post text.

## Files

```text
user-data/
├── config.toml
├── tokens.json
├── data/
│   ├── connection_context.json
│   ├── decisions.json
│   ├── scan_cursor.json
│   ├── scan_context.json
│   └── unfollow_audit.jsonl
└── exports/
    ├── candidates.csv
    ├── scan_history.csv
    └── scan_results.csv
```

`user-data/` is ignored by Git. Do not override that ignore rule or publish
these files.

## Common problems

### Callback URL not approved

The registered callback must be:

```text
http://127.0.0.1:8765/callback
```

Use `127.0.0.1`, not `localhost`, and do not add a trailing slash.

### The browser authorized the wrong account

Return to the menu and choose option `6`. Sign in to the intended X account
before approving the App again.

### API access or payment error

Check:

- X API credits at `console.x.com`;
- App status;
- OAuth 2.0 permissions;
- the registered callback URL; and
- the configured spending limit.

Credits shown at `console.x.ai` are separate.

### Scan blocked by the local budget

Reduce the number of accounts or increase the hard scan budget in option `5`.
The CLI intentionally refuses to bypass this limit.

### A reposting account is considered active

This is intentional. The app checks whether an account has shown any X activity
without buying individual Post reads. It does not distinguish activity types.

## Direct commands

```bash
.venv/bin/x-unfollow --help
.venv/bin/x-unfollow status
.venv/bin/x-unfollow check
.venv/bin/x-unfollow refresh-count
.venv/bin/x-unfollow scan --limit 3
.venv/bin/x-unfollow scan --restart
.venv/bin/x-unfollow review
.venv/bin/x-unfollow unfollow --dry-run
```
