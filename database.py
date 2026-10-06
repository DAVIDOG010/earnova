import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DATABASE_PATH = Path(__file__).resolve().parent / "earnovabot.sqlite3"


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with database_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                username TEXT,
                balance_cents INTEGER NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                task_url TEXT,
                reward_cents INTEGER NOT NULL CHECK (reward_cents > 0),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS task_completions (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (telegram_id),
                task_id INTEGER NOT NULL REFERENCES tasks (task_id),
                task_title TEXT NOT NULL,
                reward_cents INTEGER NOT NULL CHECK (reward_cents > 0),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
                submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_or_approved_claim
                ON task_completions (user_id, task_id)
                WHERE status IN ('pending', 'approved');
            """
        )
        task_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "task_url" not in task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN task_url TEXT")



def save_user(user_id: int, first_name: str, username: str | None) -> None:
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO users (telegram_id, first_name, username)
            VALUES (?, ?, ?)
            ON CONFLICT (telegram_id) DO UPDATE SET
                first_name = excluded.first_name,
                username = excluded.username,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, first_name, username),
        )


def get_user_balance(user_id: int) -> int:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT balance_cents FROM users WHERE telegram_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["balance_cents"]) if row else 0


def list_active_tasks(user_id: int) -> list[dict]:
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                tasks.task_id,
                tasks.title,
                tasks.description,
                tasks.task_url,
                tasks.reward_cents,
                (
                    SELECT task_completions.status
                    FROM task_completions
                    WHERE task_completions.user_id = ?
                      AND task_completions.task_id = tasks.task_id
                      AND task_completions.status IN ('pending', 'approved')
                    LIMIT 1
                ) AS claim_status
            FROM tasks
            WHERE tasks.is_active = 1
            ORDER BY tasks.task_id
            """,
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_tasks(active_only: bool = False) -> list[dict]:
    query = """
        SELECT task_id, title, description, task_url, reward_cents, is_active
        FROM tasks
    """
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY task_id"
    with database_connection() as connection:
        rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


def get_task(task_id: int) -> dict | None:
    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT task_id, title, description, task_url, reward_cents, is_active
            FROM tasks
            WHERE task_id = ?
            """,
            (task_id,),
        ).fetchone()
    return dict(row) if row else None


def create_task(
    title: str,
    description: str,
    reward_cents: int,
    task_url: str | None = None,
) -> int:
    with database_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO tasks (title, description, reward_cents, task_url)
            VALUES (?, ?, ?, ?)
            """,
            (title, description, reward_cents, task_url),
        )
        return int(cursor.lastrowid)


def edit_task(
    task_id: int,
    title: str,
    description: str,
    reward_cents: int,
    task_url: str | None = None,
) -> bool:
    with database_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET title = ?,
                description = ?,
                reward_cents = ?,
                task_url = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
            """,
            (title, description, reward_cents, task_url, task_id),
        )
        return cursor.rowcount == 1


def deactivate_task(task_id: int) -> bool:
    with database_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE task_id = ? AND is_active = 1
            """,
            (task_id,),
        )
        return cursor.rowcount == 1


def submit_task(user_id: int, task_id: int) -> dict:
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        task = connection.execute(
            """
            SELECT task_id, title, reward_cents
            FROM tasks
            WHERE task_id = ? AND is_active = 1
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            return {"status": "unavailable"}

        previous = connection.execute(
            """
            SELECT status
            FROM task_completions
            WHERE user_id = ? AND task_id = ?
              AND status IN ('pending', 'approved')
            """,
            (user_id, task_id),
        ).fetchone()
        if previous is not None:
            return {"status": previous["status"]}

        cursor = connection.execute(
            """
            INSERT INTO task_completions (
                user_id, task_id, task_title, reward_cents, status
            )
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (user_id, task_id, task["title"], task["reward_cents"]),
        )
        submitted_at = connection.execute(
            "SELECT submitted_at FROM task_completions WHERE claim_id = ?",
            (cursor.lastrowid,),
        ).fetchone()["submitted_at"]
        return {
            "status": "submitted",
            "claim_id": int(cursor.lastrowid),
            "task_title": task["title"],
            "reward_cents": int(task["reward_cents"]),
            "submitted_at": submitted_at,
        }


def list_pending_claims() -> list[dict]:
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                task_completions.claim_id,
                task_completions.user_id,
                task_completions.task_title,
                task_completions.reward_cents,
                task_completions.submitted_at,
                users.first_name,
                users.username
            FROM task_completions
            JOIN users ON users.telegram_id = task_completions.user_id
            WHERE task_completions.status = 'pending'
            ORDER BY task_completions.submitted_at, task_completions.claim_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def approve_claim(claim_id: int) -> dict:
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT claim_id, user_id, task_title, reward_cents, status
            FROM task_completions
            WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
        if row is None:
            return {"status": "not_found"}
        if row["status"] != "pending":
            return {"status": "already_reviewed"}

        cursor = connection.execute(
            """
            UPDATE task_completions
            SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP
            WHERE claim_id = ? AND status = 'pending'
            """,
            (claim_id,),
        )
        if cursor.rowcount != 1:
            return {"status": "already_reviewed"}

        connection.execute(
            """
            UPDATE users
            SET balance_cents = balance_cents + ?, updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
            """,
            (row["reward_cents"], row["user_id"]),
        )
        balance = connection.execute(
            "SELECT balance_cents FROM users WHERE telegram_id = ?",
            (row["user_id"],),
        ).fetchone()
        if balance is None:
            raise RuntimeError("Claim owner is missing from the users table.")
        return {
            "status": "approved",
            "user_id": int(row["user_id"]),
            "task_title": row["task_title"],
            "reward_cents": int(row["reward_cents"]),
            "balance_cents": int(balance["balance_cents"]),
        }


def reject_claim(claim_id: int) -> dict:
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT user_id, task_title, status
            FROM task_completions
            WHERE claim_id = ?
            """,
            (claim_id,),
        ).fetchone()
        if row is None:
            return {"status": "not_found"}
        if row["status"] != "pending":
            return {"status": "already_reviewed"}

        connection.execute(
            """
            UPDATE task_completions
            SET status = 'rejected', reviewed_at = CURRENT_TIMESTAMP
            WHERE claim_id = ? AND status = 'pending'
            """,
            (claim_id,),
        )
        return {
            "status": "rejected",
            "user_id": int(row["user_id"]),
            "task_title": row["task_title"],
        }
