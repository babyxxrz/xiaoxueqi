import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

db_path = Path("data/app.db")

if not db_path.exists():
    raise FileNotFoundError(f"数据库文件不存在：{db_path.resolve()}")

backup_path = db_path.with_name(
    f"app_backup_before_clear_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
)

shutil.copy2(db_path, backup_path)
print(f"已备份数据库到：{backup_path}")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
existing_tables = {row[0] for row in cur.fetchall()}

tables_to_clear = [
    "recognition_records",
    "alert_events",
    "operation_logs",
]

for table in tables_to_clear:
    if table in existing_tables:
        cur.execute(f"DELETE FROM {table}")
        print(f"已清空表：{table}")
    else:
        print(f"表不存在，跳过：{table}")

if "sqlite_sequence" in existing_tables:
    placeholders = ",".join(["?"] * len(tables_to_clear))
    cur.execute(
        f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
        tables_to_clear,
    )
    print("已重置自增 ID")

conn.commit()
conn.close()

print("完成：识别记录、告警记录、操作日志已清空。")
