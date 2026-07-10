import argparse
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在：{DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_users() -> None:
    conn = get_connection()

    try:
        rows = conn.execute(
            """
            SELECT id, username, email, role, created_at
            FROM users
            ORDER BY id
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("当前没有用户。")
        return

    print("-" * 90)
    print(f"{'ID':<6}{'用户名':<22}{'邮箱':<38}{'角色':<12}{'注册时间'}")
    print("-" * 90)

    for row in rows:
        print(
            f"{row['id']:<6}"
            f"{row['username']:<22}"
            f"{row['email']:<38}"
            f"{row['role']:<12}"
            f"{row['created_at']}"
        )


def promote_accounts(accounts: list[str]) -> None:
    conn = get_connection()

    try:
        for account in accounts:
            account = account.strip()

            row = conn.execute(
                """
                SELECT id, username, email, role
                FROM users
                WHERE username = ? OR email = ?
                """,
                (account, account),
            ).fetchone()

            if row is None:
                print(f"[未找到] {account}")
                continue

            if row["role"] == "admin":
                print(f"[已是管理员] {row['username']} ({row['email']})")
                continue

            conn.execute(
                """
                UPDATE users
                SET role = 'admin'
                WHERE id = ?
                """,
                (row["id"],),
            )

            print(f"[提升成功] {row['username']} ({row['email']}) -> admin")

        conn.commit()
    finally:
        conn.close()


def demote_accounts(accounts: list[str]) -> None:
    conn = get_connection()

    try:
        for account in accounts:
            account = account.strip()

            row = conn.execute(
                """
                SELECT id, username, email, role
                FROM users
                WHERE username = ? OR email = ?
                """,
                (account, account),
            ).fetchone()

            if row is None:
                print(f"[未找到] {account}")
                continue

            if row["role"] == "user":
                print(f"[已经是普通用户] {row['username']} ({row['email']})")
                continue

            if row["username"] == "admin":
                print("[拒绝操作] 不允许降级默认 admin 账号")
                continue

            conn.execute(
                """
                UPDATE users
                SET role = 'user'
                WHERE id = ?
                """,
                (row["id"],),
            )

            print(f"[降级成功] {row['username']} ({row['email']}) -> user")

        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="管理系统用户角色")

    parser.add_argument(
        "--list",
        action="store_true",
        help="列出全部用户",
    )

    parser.add_argument(
        "--promote",
        nargs="+",
        metavar="ACCOUNT",
        help="按照用户名或邮箱提升为管理员",
    )

    parser.add_argument(
        "--demote",
        nargs="+",
        metavar="ACCOUNT",
        help="按照用户名或邮箱降级为普通用户",
    )

    args = parser.parse_args()

    if args.list:
        list_users()
        return

    if args.promote:
        promote_accounts(args.promote)
        print()
        list_users()
        return

    if args.demote:
        demote_accounts(args.demote)
        print()
        list_users()
        return

    parser.print_help()


if __name__ == "__main__":
    main()