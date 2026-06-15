#!/bin/bash
DB=/opt/tofuhouse/news_agent/news.db
BACKUP_DIR=/opt/tofuhouse/news_agent/backups
DATE=$(date +%Y%m%d)

sqlite3 "$DB" ".backup $BACKUP_DIR/news_$DATE.db"
find "$BACKUP_DIR" -name 'news_*.db' -type f | sort | head -n -2 | xargs -r rm -f
