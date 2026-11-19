from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "nextbuy.db"
BACKUP_DIR = ROOT / "backups"
KEEP_BACKUPS = 14


def main() -> None:
    if not DATABASE.is_file():
        raise SystemExit("Database not found: %s" % DATABASE)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f-UTC")
    destination = BACKUP_DIR / ("nextbuy-%s.sqlite3" % stamp)
    temporary = destination.with_suffix(destination.suffix + ".tmp")

    source = sqlite3.connect(str(DATABASE), timeout=30)
    target = None
    try:
        target = sqlite3.connect(str(temporary), timeout=30)
        source.backup(target, pages=256, sleep=0.1)
        target.close()
        target = None
        temporary.replace(destination)
    except Exception:
        if target is not None:
            target.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        source.close()

    backups = sorted(
        (path for path in BACKUP_DIR.glob("nextbuy-*.sqlite3") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[KEEP_BACKUPS:]:
        old_backup.unlink()
    print("Backup created: %s" % destination)
    print("Retained %d backup(s)." % min(len(backups), KEEP_BACKUPS))


if __name__ == "__main__":
    main()
