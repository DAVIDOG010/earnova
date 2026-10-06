import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database


REFERRAL_BOT_USERNAME = "EarnovaRewardsHQBotBot"
ADMIN_FORM_KEY = "admin_task_form"
MAX_TASK_TITLE_LENGTH = 100
MAX_TASK_DESCRIPTION_LENGTH = 1000
MAX_SQLITE_INTEGER = 2**63 - 1

WELCOME_MESSAGE = (
    "Welcome to Earnova 💰\n\n"
    "Earn money by completing tasks, watching ads, and inviting friends."
)

BALANCE_BUTTON = "💰 Balance"
PROFILE_BUTTON = "👤 Profile"
TASKS_BUTTON = "📋 Tasks"
INVITE_BUTTON = "👥 Invite Friends"
ADMIN_BUTTON = "🛠 Admin"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BALANCE_BUTTON, PROFILE_BUTTON],
        [TASKS_BUTTON, INVITE_BUTTON],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
ADMIN_MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BALANCE_BUTTON, PROFILE_BUTTON],
        [TASKS_BUTTON, INVITE_BUTTON],
        [ADMIN_BUTTON],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def read_admin_id() -> int:
    value = os.environ.get("ADMIN_ID", "")
    if not value.isascii() or not value.isdecimal():
        raise RuntimeError(
            "Missing or invalid ADMIN_ID. Add your numeric Telegram user ID to Replit Secrets."
        )
    admin_id = int(value)
    if admin_id <= 0:
        raise RuntimeError("ADMIN_ID must be a positive numeric Telegram user ID.")
    return admin_id


def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    return (
        user is not None
        and user.id == context.application.bot_data.get("admin_id")
    )


def format_balance(balance_cents: int) -> str:
    dollars, cents = divmod(balance_cents, 100)
    return f"${dollars}.{cents:02d}"


def is_valid_task_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def save_effective_user(update: Update) -> int | None:
    user = update.effective_user
    if user is None:
        return None
    database.save_user(user.id, user.first_name or "Unknown", user.username)
    return user.id


def admin_panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Add task", callback_data="admin:add"),
                InlineKeyboardButton("✏️ Edit task", callback_data="admin:edit"),
            ],
            [
                InlineKeyboardButton(
                    "⏸ Deactivate task", callback_data="admin:deactivate"
                ),
                InlineKeyboardButton("📋 View tasks", callback_data="admin:view"),
            ],
            [
                InlineKeyboardButton(
                    "🕒 Pending claims", callback_data="admin:pending"
                )
            ],
        ]
    )


def claim_decision_markup(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"admin:approve:{claim_id}",
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"admin:reject:{claim_id}",
                ),
            ]
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    save_effective_user(update)
    keyboard = ADMIN_MAIN_KEYBOARD if is_admin(update, context) else MAIN_KEYBOARD
    await update.message.reply_text(WELCOME_MESSAGE, reply_markup=keyboard)


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = save_effective_user(update)
    message = update.effective_message
    if user_id is not None and message is not None:
        balance = format_balance(database.get_user_balance(user_id))
        await message.reply_text(f"Your current balance: {balance}")


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = save_effective_user(update)
    user = update.effective_user
    message = update.effective_message
    if user_id is None or user is None or message is None:
        return

    username = f"@{user.username}" if user.username else "Not set"
    balance = format_balance(database.get_user_balance(user_id))
    await message.reply_text(
        "👤 Profile\n"
        f"First name: {user.first_name or 'Unknown'}\n"
        f"Username: {username}\n"
        f"Telegram ID: {user_id}\n"
        f"Balance: {balance}"
    )


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = save_effective_user(update)
    message = update.effective_message
    if user_id is None or message is None:
        return

    tasks = database.list_active_tasks(user_id)
    if not tasks:
        await message.reply_text("No tasks available yet.")
        return

    for task in tasks:
        reward = format_balance(task["reward_cents"])
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"📋 Open task · {reward}",
                        callback_data=f"task:open:{task['task_id']}",
                    )
                ]
            ]
        )
        await message.reply_text(
            f"{task['title']}\n\n{task['description']}\n\nReward: {reward}",
            reply_markup=markup,
        )


async def open_task_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user_id = save_effective_user(update)
    if query is None or user_id is None:
        return

    await query.answer()
    task_id = int(query.data.rsplit(":", 1)[1])
    task = next(
        (
            item
            for item in database.list_active_tasks(user_id)
            if item["task_id"] == task_id
        ),
        None,
    )
    if task is None or query.message is None:
        if query.message is not None:
            await query.message.reply_text("This task is no longer available.")
        return

    reward = format_balance(task["reward_cents"])
    text = f"{task['title']}\n\n{task['description']}\n\nReward: {reward}"
    buttons = []
    if task["task_url"] and is_valid_task_url(task["task_url"]):
        buttons.append(
            InlineKeyboardButton("▶️ Watch Video", url=task["task_url"])
        )
    else:
        text += "\n\nThe admin has not configured a task URL yet."

    if task["claim_status"] == "pending":
        text += "\n\nStatus: Pending admin review."
    elif task["claim_status"] == "approved":
        text += "\n\nStatus: Completed."
    else:
        buttons.append(
            InlineKeyboardButton(
                "☑ Submit for Review",
                callback_data=f"task:submit:{task_id}",
            )
        )

    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    await query.message.reply_text(text, reply_markup=markup)


async def submit_task_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user_id = save_effective_user(update)
    if query is None or user_id is None:
        return

    await query.answer()
    task_id = int(query.data.rsplit(":", 1)[1])
    result = database.submit_task(user_id, task_id)
    status = result["status"]
    if status == "submitted":
        user = update.effective_user
        username = f"@{user.username}" if user is not None and user.username else "Not set"
        first_name = user.first_name if user is not None else "Unknown"
        admin_message = (
            f"New task submission #{result['claim_id']}\n"
            f"Task: {result['task_title']}\n"
            f"User: {first_name} ({username})\n"
            f"Telegram ID: {user_id}\n"
            f"Reward: {format_balance(result['reward_cents'])}\n"
            f"Status: Pending\n"
            f"Submitted: {result['submitted_at']}"
        )
        try:
            await context.bot.send_message(
                chat_id=context.application.bot_data["admin_id"],
                text=admin_message,
                reply_markup=claim_decision_markup(result["claim_id"]),
            )
            response = (
                "Task submitted for admin approval. Your balance will update after "
                "approval, and the admin has been notified."
            )
        except TelegramError:
            response = (
                "Task submission saved as pending, but the admin notification could "
                "not be delivered. The admin can review it from /admin."
            )
    else:
        responses = {
            "unavailable": "This task is no longer available.",
            "pending": "Your submission is already waiting for admin approval.",
            "approved": "You have already completed this task and received its reward.",
        }
        response = responses[status]
    if query.message is not None:
        await query.message.reply_text(response)


async def show_invite_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user_id = save_effective_user(update)
    message = update.effective_message
    if user_id is not None and message is not None:
        referral_link = (
            f"https://t.me/{REFERRAL_BOT_USERNAME}?start=ref_{user_id}"
        )
        await message.reply_text(f"Your personal referral link:\n{referral_link}")


async def show_admin_panel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    if not is_admin(update, context):
        await message.reply_text("This section is for admins only.")
        return
    await message.reply_text("Earnova task administration:", reply_markup=admin_panel_markup())


def task_selector_markup(
    tasks: list[dict], callback_prefix: str
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"#{task['task_id']} {task['title'][:42]} · "
                f"{format_balance(task['reward_cents'])}",
                callback_data=f"admin:{callback_prefix}:{task['task_id']}",
            )
        ]
        for task in tasks
    ]
    rows.append([InlineKeyboardButton("Back to admin menu", callback_data="admin:menu")])
    return InlineKeyboardMarkup(rows)


def begin_admin_form(
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    task_id: int | None = None,
    current_task_url: str | None = None,
) -> None:
    form = {"action": action, "step": "title", "values": {}}
    if task_id is not None:
        form["task_id"] = task_id
        form["current_task_url"] = current_task_url
    context.user_data[ADMIN_FORM_KEY] = form


async def handle_admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None:
        return
    if not is_admin(update, context):
        await query.answer("This section is for admins only.", show_alert=True)
        return

    await query.answer()
    data = query.data or ""
    message = query.message
    if message is None:
        return

    if data in {"admin:menu", "admin:view"}:
        if data == "admin:menu":
            await message.reply_text(
                "Earnova task administration:", reply_markup=admin_panel_markup()
            )
        else:
            tasks = database.list_tasks()
            if not tasks:
                await message.reply_text("There are no tasks yet.")
            for task in tasks:
                status = "Active" if task["is_active"] else "Inactive"
                await message.reply_text(
                    f"Task #{task['task_id']} — {task['title']}\n"
                    f"{task['description']}\n"
                    f"Reward: {format_balance(task['reward_cents'])}\n"
                    f"Task URL: {task['task_url'] or 'Not set'}\n"
                    f"Status: {status}"
                )
        return

    if data == "admin:add":
        begin_admin_form(context, "add")
        await message.reply_text(
            f"Send the task title (up to {MAX_TASK_TITLE_LENGTH} characters). "
            "Use /cancel to stop."
        )
        return

    if data in {"admin:edit", "admin:deactivate"}:
        action = "edit_task" if data == "admin:edit" else "deactivate_task"
        tasks = database.list_tasks(active_only=data == "admin:deactivate")
        if not tasks:
            response = (
                "There are no active tasks to deactivate."
                if data == "admin:deactivate"
                else "There are no tasks to edit."
            )
            await message.reply_text(response)
            return
        prompt = "Choose a task to edit:" if data == "admin:edit" else "Choose a task to deactivate:"
        await message.reply_text(
            prompt,
            reply_markup=task_selector_markup(tasks, action),
        )
        return

    if data == "admin:pending":
        claims = database.list_pending_claims()
        if not claims:
            await message.reply_text("There are no pending task submissions.")
            return
        for claim in claims:
            username = f"@{claim['username']}" if claim["username"] else "Not set"
            await message.reply_text(
                f"Submission #{claim['claim_id']}\n"
                f"Task: {claim['task_title']}\n"
                f"User: {claim['first_name']} ({username})\n"
                f"Telegram ID: {claim['user_id']}\n"
                f"Reward: {format_balance(claim['reward_cents'])}\n"
                f"Submitted: {claim['submitted_at']}",
                reply_markup=claim_decision_markup(claim["claim_id"]),
            )
        return

    parts = data.split(":")
    if len(parts) != 3:
        await message.reply_text("That admin action is not available.")
        return
    action, raw_id = parts[1], parts[2]
    if not raw_id.isdecimal():
        await message.reply_text("That admin action is not available.")
        return
    record_id = int(raw_id)

    if action == "edit_task":
        task = database.get_task(record_id)
        if task is None:
            await message.reply_text("That task no longer exists.")
            return
        begin_admin_form(
            context,
            "edit",
            record_id,
            current_task_url=task["task_url"],
        )
        await message.reply_text(
            f"Send the new title for “{task['title']}” "
            f"(up to {MAX_TASK_TITLE_LENGTH} characters). Use /cancel to stop."
        )
        return

    if action == "deactivate_task":
        if database.deactivate_task(record_id):
            await message.reply_text(f"Task #{record_id} is now inactive.")
        else:
            await message.reply_text("That task is missing or already inactive.")
        return

    if action == "approve":
        result = database.approve_claim(record_id)
        if result["status"] == "approved":
            balance = format_balance(result["balance_cents"])
            reward = format_balance(result["reward_cents"])
            notification_sent = True
            try:
                await context.bot.send_message(
                    chat_id=result["user_id"],
                    text=(
                        f"✅ Your task “{result['task_title']}” was approved.\n"
                        f"Reward added: {reward}\n"
                        f"Your updated balance: {balance}"
                    ),
                )
            except TelegramError:
                notification_sent = False
            extra = (
                " The user was notified."
                if notification_sent
                else " The reward was credited, but Telegram could not deliver a notification."
            )
            await message.reply_text(
                f"Approved. {reward} credited; the user's balance is now {balance}.{extra}"
            )
        elif result["status"] == "not_found":
            await message.reply_text("That submission no longer exists.")
        else:
            await message.reply_text("That submission has already been reviewed.")
        return

    if action == "reject":
        result = database.reject_claim(record_id)
        if result["status"] == "rejected":
            try:
                await context.bot.send_message(
                    chat_id=result["user_id"],
                    text=(
                        f"Your submission for “{result['task_title']}” was not approved. "
                        "You may submit it again."
                    ),
                )
            except TelegramError:
                pass
            await message.reply_text("Submission rejected. The user may submit the task again.")
        elif result["status"] == "not_found":
            await message.reply_text("That submission no longer exists.")
        else:
            await message.reply_text("That submission has already been reviewed.")
        return

    await message.reply_text("That admin action is not available.")


def parse_reward_cents(value: str) -> int | None:
    try:
        amount = Decimal(value.strip())
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0:
        return None
    cents = amount * 100
    if cents != cents.to_integral_value() or cents > MAX_SQLITE_INTEGER:
        return None
    return int(cents)


async def handle_admin_form_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    form = context.user_data.get(ADMIN_FORM_KEY)
    if form is None:
        return

    message = update.effective_message
    if message is None:
        return
    if not is_admin(update, context):
        context.user_data.pop(ADMIN_FORM_KEY, None)
        await message.reply_text("This section is for admins only.")
        raise ApplicationHandlerStop

    value = (message.text or "").strip()
    step = form["step"]
    if step == "title":
        if not value or len(value) > MAX_TASK_TITLE_LENGTH:
            await message.reply_text(
                f"Enter a title from 1 to {MAX_TASK_TITLE_LENGTH} characters."
            )
            raise ApplicationHandlerStop
        form["values"]["title"] = value
        form["step"] = "description"
        await message.reply_text(
            f"Send the task description (up to {MAX_TASK_DESCRIPTION_LENGTH} characters)."
        )
        raise ApplicationHandlerStop

    if step == "description":
        if not value or len(value) > MAX_TASK_DESCRIPTION_LENGTH:
            await message.reply_text(
                f"Enter a description from 1 to {MAX_TASK_DESCRIPTION_LENGTH} characters."
            )
            raise ApplicationHandlerStop
        form["values"]["description"] = value
        form["step"] = "reward"
        await message.reply_text("Send the reward amount in USD (for example, 1.50).")
        raise ApplicationHandlerStop

    if step == "reward":
        reward_cents = parse_reward_cents(value)
        if reward_cents is None:
            await message.reply_text(
                "Enter a positive reward in USD with at most two decimal places "
                "(for example, 1.50)."
            )
            raise ApplicationHandlerStop

        form["values"]["reward_cents"] = reward_cents
        form["step"] = "task_url"
        if form["action"] == "add":
            prompt = "Send an HTTP(S) task URL, or type skip to leave it unset."
        else:
            prompt = (
                "Send a new HTTP(S) task URL, type keep to retain the current URL, "
                "or clear to remove it."
            )
        await message.reply_text(prompt)
        raise ApplicationHandlerStop

    if step != "task_url":
        context.user_data.pop(ADMIN_FORM_KEY, None)
        await message.reply_text("The admin form expired. Open /admin to start again.")
        raise ApplicationHandlerStop

    action = form["action"]
    if action == "add" and value.casefold() == "skip":
        task_url = None
    elif action == "edit" and value.casefold() == "keep":
        task_url = form["current_task_url"]
    elif action == "edit" and value.casefold() == "clear":
        task_url = None
    elif is_valid_task_url(value):
        task_url = value
    else:
        await message.reply_text(
            "Enter a valid HTTP(S) URL. "
            "For a new task, type skip to leave it unset; when editing, type keep or clear."
        )
        raise ApplicationHandlerStop

    title = form["values"]["title"]
    description = form["values"]["description"]
    reward_cents = form["values"]["reward_cents"]
    url_status = "Task URL set." if task_url else "No task URL set."
    if action == "add":
        task_id = database.create_task(title, description, reward_cents, task_url)
        response = (
            f"Task #{task_id} created with a reward of "
            f"{format_balance(reward_cents)}. {url_status}"
        )
    else:
        task_id = form["task_id"]
        if database.edit_task(task_id, title, description, reward_cents, task_url):
            response = (
                f"Task #{task_id} updated. New reward: "
                f"{format_balance(reward_cents)}. {url_status}"
            )
        else:
            response = "That task no longer exists; no changes were saved."

    context.user_data.pop(ADMIN_FORM_KEY, None)
    await message.reply_text(response)
    raise ApplicationHandlerStop


async def cancel_admin_action(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data.pop(ADMIN_FORM_KEY, None)
    message = update.effective_message
    if message is not None:
        await message.reply_text("Admin action cancelled.")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN. Add it in Replit Secrets before starting the bot."
        )
    admin_id = read_admin_id()

    database.initialize_database()
    application = Application.builder().token(token).build()
    application.bot_data["admin_id"] = admin_id

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_admin_form_input,
        ),
        group=-1,
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", show_admin_panel))
    application.add_handler(CommandHandler("cancel", cancel_admin_action))
    application.add_handler(
        CallbackQueryHandler(
            open_task_callback,
            pattern=r"^task:open:\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            submit_task_callback,
            pattern=r"^task:submit:\d+$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(handle_admin_callback, pattern=r"^admin:")
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^💰 Balance$"),
            show_balance,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^👤 Profile$"),
            show_profile,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^📋 Tasks$"),
            show_tasks,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^👥 Invite Friends$"),
            show_invite_link,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^🛠 Admin$"),
            show_admin_panel,
        )
    )
    application.run_polling()


if __name__ == "__main__":
    main()
