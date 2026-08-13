#!/usr/bin/env python3
import argparse
import re
import sqlite3


DEVICE_SUBJECT = re.compile(r"^(?P<username>.+):device:[0-9a-fA-F-]{32,36}$")


def aliases(connection, table, column):
    values = connection.execute(
        f"SELECT DISTINCT {column} FROM {table} WHERE {column} LIKE '%:device:%'"
    ).fetchall()
    result = []
    for (value,) in values:
        match = DEVICE_SUBJECT.fullmatch(str(value or ""))
        if match:
            result.append((value, match.group("username")))
    return result


def merge(database_path, apply_changes=False):
    connection = sqlite3.connect(database_path, timeout=10)
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        owner_aliases = aliases(connection, "conversation_owners", "user_name")
        log_aliases = aliases(connection, "visit_logs", "username")
        session_aliases = aliases(connection, "gateway_sessions", "user_name")
        result = {
            "conversation_aliases": len(owner_aliases),
            "visit_log_aliases": len(log_aliases),
            "session_aliases": len(session_aliases),
            "conversation_rows": 0,
            "visit_log_rows": 0,
            "sessions_renamed": 0,
            "sessions_removed": 0,
        }
        if not apply_changes:
            return result

        connection.execute("BEGIN IMMEDIATE")
        for alias, username in owner_aliases:
            cursor = connection.execute(
                "UPDATE conversation_owners SET user_name = ? WHERE user_name = ?",
                (username, alias),
            )
            result["conversation_rows"] += cursor.rowcount
        for alias, username in log_aliases:
            cursor = connection.execute(
                "UPDATE visit_logs SET username = ? WHERE username = ?",
                (username, alias),
            )
            result["visit_log_rows"] += cursor.rowcount
        for alias, username in session_aliases:
            rows = connection.execute(
                "SELECT id, chatgpt_username FROM gateway_sessions WHERE user_name = ?",
                (alias,),
            ).fetchall()
            for session_id, chatgpt_username in rows:
                canonical_exists = connection.execute(
                    """
                    SELECT 1 FROM gateway_sessions
                     WHERE user_name = ? AND chatgpt_username = ?
                     LIMIT 1
                    """,
                    (username, chatgpt_username),
                ).fetchone()
                if canonical_exists:
                    connection.execute(
                        "DELETE FROM gateway_sessions WHERE id = ?",
                        (session_id,),
                    )
                    result["sessions_removed"] += 1
                else:
                    connection.execute(
                        "UPDATE gateway_sessions SET user_name = ? WHERE id = ?",
                        (username, session_id),
                    )
                    result["sessions_renamed"] += 1
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = merge(args.database, apply_changes=args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(mode, " ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
