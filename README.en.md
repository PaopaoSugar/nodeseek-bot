# NodeSeek Keyword Monitor Bot

Watches new topics on [NodeSeek](https://www.nodeseek.com/) and sends you a
Telegram DM with the title and link whenever one matches your keywords.

[![CI](https://github.com/PaopaoSugar/nodeseek-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/PaopaoSugar/nodeseek-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

English | [简体中文](README.md)

## Features

- **New topics only** — replies are ignored. A match is a DM, silence otherwise.
- Up to **3 keywords per user** (configurable). Plain substring matching on the
  title: no tokenizer, no regex.
- Words inside one rule are separated by spaces and mean **AND**:
  `日本 vps` matches only titles containing both `日本` and `vps`.
- Full-width/half-width and case are normalised: `ＶＰＳ` and `vps` are the same rule.
- Several matches for one user are **coalesced into a single message**.
- **Invite based** (optional): `/invite` issues a single-use code, friends join with
  `/start <code>`. Can also be a fixed reusable code per user, or registration can be
  opened to everyone.
- Admin commands: `stats` / `top` / `users` / `ban` / `unban` / `health`.
- One process, two runtime dependencies (aiogram + httpx), all state in SQLite.
- **No inbound port** required: it uses long polling.

## How it works

    ┌──────────┐  new items  ┌──────────┐  deliveries  ┌──────────┐
    │  poller  │ ──────────▶ │ matcher  │ ───────────▶ │ notifier │
    └──────────┘             └──────────┘              └──────────┘
          │                       │                          │
          └─────────────┬─────────┴──────────────────────────┘
                        ▼
                   SQLite (WAL)
                        ▲
                        │
                   ┌──────────┐
                   │   bot    │  aiogram: user and admin commands
                   └──────────┘

- Every 30 seconds it fetches NodeSeek's public RSS (a 20 item window) and
  `INSERT OR IGNORE`s the rows, so only genuinely new topics are matched.
- Matching is substring comparison: 1000 users × 3 rules = 3000 comparisons per
  new topic, well under a millisecond.
- Matches are written to the `deliveries` table before sending, so a restart or a
  repeated fetch can never deliver twice.
- **It only reads the public RSS — no account, no cookie, no credentials.**

## Deploy to a server (one command)

On Debian 12 / Ubuntu 22.04 or newer, as root:

    curl -fsSL https://raw.githubusercontent.com/PaopaoSugar/nodeseek-bot/main/scripts/install.sh | sh

It installs the system packages, creates the `nodeseek` system user, clones the repo,
builds a virtualenv, writes `/etc/nodeseek-bot/config.toml` (prompting for your bot
token and admin user id), prepares the data directory, installs and starts the systemd
service, and grants the admins. Running it twice is safe: an existing config and
database are left alone.

Updating later is one line:

    sh /opt/nodeseek-bot/scripts/update.sh

It stops the service, backs up the database, pulls the latest code, reinstalls and
restarts. `--check` only reports whether an update exists; `--branch` switches branch.

Everything else (layout, troubleshooting, upgrade, uninstall) is in
[docs/deployment.md](docs/deployment.md).

## Manual run (local or dev)

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. Get your numeric user id from [@userinfobot](https://t.me/userinfobot).
3. Install and configure:

       python3 -m venv .venv
       .venv/bin/pip install .
       cp config.example.toml config.toml
       nano config.toml      # set bot_token and admin_user_ids

4. Add yourself (the bot is invite-only, and the first user must come from the CLI):

       .venv/bin/nodeseek-bot --config config.toml --grant <your user id>

5. Run it:

       .venv/bin/nodeseek-bot --config config.toml

6. In Telegram, send `/start`, then:

       /add 日本 vps      # add a keyword
       /list              # list rules (one tap to delete)
       /test 日本 vps     # see how many of the last 24h titles would match
       /invite            # generate an invite code

For long-running use, install the systemd unit — see
[docs/deployment.md](docs/deployment.md).

CLI flags: `--config PATH`, `--maintenance` (purge expired items),
`--grant TG_USER_ID` (add a user without an invite code), `--log-level`.

## Configuration

Every key lives in [`config.example.toml`](config.example.toml):

| Key | Default | Meaning |
| --- | --- | --- |
| `telegram.bot_token` | — | **Required**, from BotFather |
| `telegram.admin_user_ids` | `[]` | Admin user ids that receive operational alerts |
| `poller.interval_seconds` | `30` | Poll interval. **Never go below 20** |
| `poller.request_timeout_seconds` | `20` | Per-request timeout |
| `poller.max_backoff_seconds` | `600` | Backoff ceiling for consecutive failures |
| `poller.overflow_threshold` | `15` | Alert when one poll inserts more than this (feed window is 20) |
| `poller.feed_url` | `https://rss.nodeseek.com/` | Feed endpoint |
| `notifier.global_rate_per_second` | `25` | Global send rate (Telegram allows ~30/s) |
| `notifier.max_attempts` | `3` | Send retries before giving up |
| `notifier.coalesce_window_seconds` | `20` | Coalescing window per user |
| `notifier.max_message_chars` | `3500` | Split messages above this length |
| `limits.max_rules_per_user` | `3` | Keywords per user |
| `limits.invite_required` | `true` | `false` opens registration to anyone |
| `limits.invite_mode` | `one_time` | `one_time` codes, or `fixed` (one reusable code each) |
| `limits.invite_quota_per_user` | `3` | `one_time` only: unused codes per user |
| `storage.db_path` | `data/bot.db` | SQLite path; use `/var/lib/...` in production |
| `storage.retention_days` | `14` | Item retention, applied by `--maintenance` |

**Why 30 seconds**: the NodeSeek feed sits behind Cloudflare with
`cf-cache-status: DYNAMIC`, meaning it is **not cached — every request hits the
origin**. 30 seconds is already 2880 requests/day; polling faster only annoys the
origin. The feed also has no `ETag`/`Last-Modified`, so conditional requests
cannot save bandwidth either.

## Deployment

Short version: on Debian 12, `apt install python3-venv`, install into a venv,
drop in the systemd unit, and run `--maintenance` from cron once a day.
Full commands: [docs/deployment.md](docs/deployment.md).

## FAQ

**Why is the latency "tens of seconds" instead of "under 30 seconds"?**
End-to-end latency = feed freshness + average polling wait (15 s) + matching
(< 1 s) + delivery (1–2 s). Feed freshness is not ours to control: measured
freshness is tens of seconds to a few minutes. We minimise the part we own; we
cannot promise an absolute ceiling.

**Why NodeSeek only?**
linux.do's RSS is behind a Cloudflare Managed Challenge. Four TLS fingerprints ×
three paths (`/latest.rss`, `/latest.json`, the homepage) all returned
`403 cf-mitigated: challenge`. Forcing a browser fingerprint through is an
adversarial game that breaks the moment Cloudflare changes anything, so this
project does not do it. The `Source` protocol is open — a new site only needs a
parser.

**Why match titles only, not full text?**
The `description` element is **optional** (3 of 20 items lacked it in testing) and
is often noise like "RT" or "bump". Full-text matching produces enough false
positives that users mute the bot. Titles are enough.

**I'm not getting notifications.**
Send `/test <keyword>` first: it tells you whether the last 24 hours would have
matched. No match means your keyword is too narrow; a match but no DM is a bug.
Also check you are not banned and that you have not hit the rule limit (`/list`).

**Will I get banned? What should I do?**
Keep the default 30 seconds. Do not deploy several copies, do not drop below 20
seconds, and do not run other high-rate outbound scripts on the same box.

## Disclaimer

- This project reads NodeSeek's **public RSS** only; it uses no account, cookie,
  or login state.
- It is **not affiliated with, nor endorsed by**, NodeSeek.
- Post content belongs to its original authors; this project forwards only the
  **title** and the **original link**, never the full text.
- Keep polling polite. The defaults already do; **do not lower them**.
- You are responsible for complying with the target site's terms of service.

## License

[MIT](LICENSE)
