# EarnovaBot

A Telegram bot with a welcome message, balance, profile, referral links, and
admin-approved tasks. User profiles, balances, tasks, and task submissions are
stored in the local `earnovabot.sqlite3` database.

## Run

Add a Replit Secret named `TELEGRAM_BOT_TOKEN`, then start the bot with:

```sh
uv run python main.py
```

The bot uses Telegram long polling.

Set the numeric Telegram user ID allowed to manage tasks as the `ADMIN_ID`
Replit Secret. The admin can open task management with `/admin` or the Admin
button. Task submissions remain pending until approved; approval credits the
reward once and sends the user their updated balance.

Admins can optionally add an HTTP(S) task URL when creating or editing a task.
When configured, the task details show a Watch Video link followed by Submit
for Review. Telegram does not tell the bot when a URL button is tapped, so task
completion is still verified through admin approval.
