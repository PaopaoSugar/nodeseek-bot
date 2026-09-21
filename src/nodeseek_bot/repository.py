"""Data access helpers."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from nodeseek_bot.db import Database
from nodeseek_bot.matcher import RuleLimitExceeded, expand_rules

# ``max_uses`` doubles as the flag for a fixed code: nothing else uses a
# number this large, and the senders are internal.
FIXED_MAX_USES = 1_000_000


@dataclass(frozen=True, slots=True)
class NewItem:
    guid: str
    title: str
    title_norm: str
    link: str
    published_at: str
    fetched_at: str
    author: str | None = None
    category: str | None = None
    excerpt: str | None = None


class ItemRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_many(self, items: list[NewItem]) -> list[str]:
        inserted: list[str] = []
        with self._db.tx() as conn:
            for item in items:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO items
                        (guid, title, title_norm, link, author, category,
                         published_at, fetched_at, excerpt)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.guid,
                        item.title,
                        item.title_norm,
                        item.link,
                        item.author,
                        item.category,
                        item.published_at,
                        item.fetched_at,
                        item.excerpt,
                    ),
                )
                if cur.rowcount == 1:
                    inserted.append(item.guid)
        return inserted

    def recent_titles(self, hours: int) -> list[tuple[str, str]]:
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT title, title_norm FROM items
                WHERE fetched_at >= strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?)
                ORDER BY published_at DESC
                """,
                (f"-{hours} hours",),
            ).fetchall()
        return [(row["title"], row["title_norm"]) for row in rows]

    def purge_older_than(self, days: int) -> int:
        with self._db.tx() as conn:
            cur = conn.execute(
                """
                DELETE FROM items
                WHERE fetched_at < strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?)
                """,
                (f"-{days} days",),
            )
        return cur.rowcount


@dataclass(frozen=True, slots=True)
class RuleRow:
    id: int
    user_id: int
    keyword_raw: str
    keyword_norm: str


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ensure(self, *, tg_user_id: int, username: str | None) -> int:
        """Insert the user if new, refresh the username, and return the row id."""
        with self._db.tx() as conn:
            conn.execute(
                """
                INSERT INTO users (tg_user_id, username, created_at)
                VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
                ON CONFLICT(tg_user_id) DO UPDATE
                    SET username = COALESCE(excluded.username, users.username)
                """,
                (tg_user_id, username),
            )
            row = conn.execute(
                "SELECT id FROM users WHERE tg_user_id = ?", (tg_user_id,)
            ).fetchone()
        return int(row["id"])

    def set_banned(self, tg_user_id: int, banned: bool) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE users SET is_banned = ? WHERE tg_user_id = ?",
                (1 if banned else 0, tg_user_id),
            )
        return cur.rowcount > 0

    def set_notify_enabled(self, tg_user_id: int, enabled: bool) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute(
                "UPDATE users SET notify_enabled = ? WHERE tg_user_id = ?",
                (1 if enabled else 0, tg_user_id),
            )
        return cur.rowcount > 0

    def find_by_tg_id(self, tg_user_id: int) -> int | None:
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT id FROM users WHERE tg_user_id = ?", (tg_user_id,)
            ).fetchone()
        return None if row is None else int(row["id"])

    def set_inviter(self, user_id: int, inviter_id: int) -> None:
        with self._db.tx() as conn:
            conn.execute("UPDATE users SET invited_by = ? WHERE id = ?", (inviter_id, user_id))


class RuleRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, user_id: int, keyword_raw: str, *, limit: int) -> int:
        keyword_norm = expand_rules(keyword_raw)
        if keyword_norm is None:
            raise ValueError("blank or oversized keyword")
        with self._db.tx() as conn:
            rows = conn.execute(
                "SELECT keyword_norm FROM rules WHERE user_id = ?", (user_id,)
            ).fetchall()
            existing = {row["keyword_norm"] for row in rows}
            if keyword_norm in existing:
                raise ValueError("duplicate rule")
            if len(existing) >= limit:
                raise RuleLimitExceeded(f"rule limit of {limit} reached")
            cur = conn.execute(
                """
                INSERT INTO rules (user_id, keyword_raw, keyword_norm, created_at)
                VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
                """,
                (user_id, keyword_raw.strip(), keyword_norm),
            )
        return int(cur.lastrowid)

    def list_for_user(self, user_id: int) -> list[RuleRow]:
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, keyword_raw, keyword_norm FROM rules
                WHERE user_id = ?
                ORDER BY id
                """,
                (user_id,),
            ).fetchall()
        return [
            RuleRow(
                id=row["id"],
                user_id=row["user_id"],
                keyword_raw=row["keyword_raw"],
                keyword_norm=row["keyword_norm"],
            )
            for row in rows
        ]

    def delete(self, user_id: int, rule_id: int) -> bool:
        with self._db.tx() as conn:
            cur = conn.execute("DELETE FROM rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
        return cur.rowcount > 0

    def clear(self, user_id: int) -> int:
        with self._db.tx() as conn:
            cur = conn.execute("DELETE FROM rules WHERE user_id = ?", (user_id,))
        return cur.rowcount

    def all_enabled(self) -> list[RuleRow]:
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.user_id, r.keyword_raw, r.keyword_norm
                FROM rules AS r
                JOIN users AS u ON u.id = r.user_id
                WHERE r.enabled = 1 AND u.is_banned = 0 AND u.notify_enabled = 1
                ORDER BY r.id
                """
            ).fetchall()
        return [
            RuleRow(
                id=row["id"],
                user_id=row["user_id"],
                keyword_raw=row["keyword_raw"],
                keyword_norm=row["keyword_norm"],
            )
            for row in rows
        ]

    def top_keywords(self, *, limit: int) -> list[tuple[str, int]]:
        """Most subscribed keywords, normalised, ties broken alphabetically."""
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT r.keyword_norm AS keyword_norm, COUNT(*) AS n
                FROM rules AS r
                JOIN users AS u ON u.id = r.user_id
                WHERE r.enabled = 1 AND u.is_banned = 0
                GROUP BY r.keyword_norm
                ORDER BY n DESC, r.keyword_norm ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [(row["keyword_norm"], row["n"]) for row in rows]


class DeliveryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_pending(self, pairs: list[tuple[str, int, int]]) -> int:
        """Insert (item_guid, rule_id, user_id) rows, skipping duplicates."""
        created = 0
        with self._db.tx() as conn:
            for item_guid, rule_id, user_id in pairs:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO deliveries
                        (item_guid, rule_id, user_id, created_at)
                    VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
                    """,
                    (item_guid, rule_id, user_id),
                )
                created += cur.rowcount
        return created

    def pending_users(self) -> list[int]:
        with self._db.tx() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM deliveries WHERE status = 'pending' ORDER BY user_id"
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def claim_for_user(self, user_id: int) -> list[PendingDelivery]:
        with self._db.tx() as conn:
            rows = conn.execute(
                """
                SELECT d.id AS delivery_id, d.rule_id AS rule_id, d.user_id AS user_id,
                       u.tg_user_id AS tg_user_id, r.keyword_raw AS keyword_raw,
                       i.title AS title, i.link AS link
                FROM deliveries AS d
                JOIN items AS i ON i.guid = d.item_guid
                JOIN rules AS r ON r.id = d.rule_id
                JOIN users AS u ON u.id = d.user_id
                WHERE d.status = 'pending' AND d.user_id = ?
                ORDER BY i.published_at ASC
                """,
                (user_id,),
            ).fetchall()
        return [
            PendingDelivery(
                delivery_id=row["delivery_id"],
                rule_id=row["rule_id"],
                user_id=row["user_id"],
                tg_user_id=row["tg_user_id"],
                keyword_raw=row["keyword_raw"],
                title=row["title"],
                link=row["link"],
            )
            for row in rows
        ]

    def mark_sent(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._db.tx() as conn:
            conn.execute(
                f"""
                UPDATE deliveries
                SET status = 'sent',
                    sent_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
                    attempts = attempts + 1
                WHERE id IN ({_placeholders(len(ids))})
                """,
                ids,
            )

    def mark_failed(self, ids: list[int], error: str, max_attempts: int) -> int:
        """Bump attempt counters, then fail the rows that ran out of retries."""
        if not ids:
            return 0
        with self._db.tx() as conn:
            conn.execute(
                f"""
                UPDATE deliveries
                SET attempts = attempts + 1, error = ?
                WHERE id IN ({_placeholders(len(ids))})
                """,
                [error, *ids],
            )
            cur = conn.execute(
                f"""
                UPDATE deliveries
                SET status = 'failed'
                WHERE id IN ({_placeholders(len(ids))}) AND attempts >= ?
                """,
                [*ids, max_attempts],
            )
        return cur.rowcount


class MetaRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> str | None:
        with self._db.tx() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        with self._db.tx() as conn:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    delivery_id: int
    rule_id: int
    user_id: int
    tg_user_id: int
    keyword_raw: str
    title: str
    link: str


def _placeholders(count: int) -> str:
    return ", ".join("?" * count)


class InviteQuotaExceeded(Exception):
    """Raised when a user already holds their maximum number of unused codes."""


class InviteRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def outstanding(self, created_by: int) -> int:
        """Unused, unexpired codes still held by *created_by*."""
        with self._db.tx() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM invites
                WHERE created_by = ? AND used_count < max_uses
                  AND (
                    expires_at IS NULL
                    OR expires_at > strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
                  )
                """,
                (created_by,),
            ).fetchone()
        return int(row["n"])

    def issue(self, created_by: int, *, quota: int) -> list[str]:
        if self.outstanding(created_by) >= quota:
            raise InviteQuotaExceeded(f"invite quota of {quota} reached")
        code = secrets.token_urlsafe(9)
        with self._db.tx() as conn:
            conn.execute(
                """
                INSERT INTO invites (code, created_by, max_uses, created_at)
                VALUES (?, ?, 1, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))
                """,
                (code, created_by),
            )
        return [code]

    def fixed(self, created_by: int) -> str:
        """The stable, reusable code of *created_by*, created on first use.

        A fixed code is stored as an ordinary invite with a use cap nobody
        will reach, so the schema does not need a second kind of row.
        """
        with self._db.tx() as conn:
            row = conn.execute(
                "SELECT code FROM invites WHERE created_by = ? AND max_uses >= ? "
                "ORDER BY created_at LIMIT 1",
                (created_by, FIXED_MAX_USES),
            ).fetchone()
            if row is not None:
                return str(row["code"])
            code = secrets.token_urlsafe(9)
            conn.execute(
                "INSERT INTO invites (code, created_by, max_uses, created_at) "
                "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))",
                (code, created_by, FIXED_MAX_USES),
            )
        return code

    def redeem(self, code: str) -> int | None:
        """Consume one use of *code*; returns the inviter's user id, or None."""
        with self._db.tx() as conn:
            row = conn.execute(
                """
                SELECT created_by, used_count, max_uses, expires_at
                FROM invites WHERE code = ?
                """,
                (code,),
            ).fetchone()
            if row is None or row["used_count"] >= row["max_uses"]:
                return None
            expires_at = row["expires_at"]
            if expires_at is not None:
                now = conn.execute(
                    "SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now') AS now"
                ).fetchone()["now"]
                if str(expires_at) <= str(now):
                    return None
            conn.execute("UPDATE invites SET used_count = used_count + 1 WHERE code = ?", (code,))
            return int(row["created_by"])
