import argparse
import logging
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import db
from notifications import NotificationService
from raw_feed_digest import build_raw_feed_digest, date_range_for_frequency
from raw_feed_pdf import render_raw_feed_digest_pdf


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


MAX_WEBHOOK_BYTES = 1800


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Build a raw-feed digest from existing items and send it to WeCom webhook."
    )
    parser.add_argument("--user-id", type=int, help="User id whose raw-feed subscription should be used")
    parser.add_argument("--user-email", help="User email whose raw-feed subscription should be used")
    parser.add_argument("--date-from", help="Inclusive start date, YYYY-MM-DD")
    parser.add_argument("--date-to", help="Inclusive end date, YYYY-MM-DD; default is today")
    parser.add_argument("--days", type=int, help="Number of days before date-to when date-from is omitted")
    parser.add_argument("--format", choices=("pdf", "text"), default="pdf", help="Send as PDF file or chunked text")
    parser.add_argument("--max-bytes", type=int, default=MAX_WEBHOOK_BYTES, help="Max UTF-8 bytes per text message")
    parser.add_argument("--dry-run", action="store_true", help="Build digest/PDF without sending to WeCom")
    parser.add_argument("--list-users", action="store_true", help="List users and exit")
    return parser.parse_args()


def _list_users():
    for user in db.get_all_users():
        print(f"{user['id']}\t{user['email']}")


def _select_user(args):
    users = [dict(u) for u in db.get_all_users()]
    if args.user_id:
        for user in users:
            if int(user["id"]) == args.user_id:
                return user
        raise SystemExit(f"No user found for --user-id {args.user_id}")

    if args.user_email:
        wanted = args.user_email.strip().lower()
        for user in users:
            if (user.get("email") or "").strip().lower() == wanted:
                return user
        raise SystemExit(f"No user found for --user-email {args.user_email}")

    if len(users) == 1:
        return users[0]

    raise SystemExit("Specify --user-id or --user-email. Use --list-users to inspect available users.")


def _date_range(args, frequency_days):
    date_to = date.fromisoformat(args.date_to) if args.date_to else date.today()
    if args.date_from:
        return args.date_from, date_to.isoformat()
    if args.days:
        return (date_to - timedelta(days=max(1, args.days))).isoformat(), date_to.isoformat()
    return date_range_for_frequency(frequency_days, date_to=date_to)


def _split_by_utf8_bytes(text, max_bytes):
    max_bytes = max(500, int(max_bytes or MAX_WEBHOOK_BYTES))
    chunks = []
    current = []
    current_bytes = 0

    for line in text.splitlines():
        line_with_newline = line + "\n"
        line_bytes = len(line_with_newline.encode("utf-8"))
        if current and current_bytes + line_bytes > max_bytes:
            chunks.append("".join(current).rstrip())
            current = []
            current_bytes = 0

        if line_bytes <= max_bytes:
            current.append(line_with_newline)
            current_bytes += line_bytes
            continue

        for char in line_with_newline:
            char_bytes = len(char.encode("utf-8"))
            if current and current_bytes + char_bytes > max_bytes:
                chunks.append("".join(current).rstrip())
                current = []
                current_bytes = 0
            current.append(char)
            current_bytes += char_bytes

    if current:
        chunks.append("".join(current).rstrip())
    return [chunk for chunk in chunks if chunk]


def _build_digest(args):
    user = _select_user(args)
    sub = db.get_raw_feed_subscription(user["id"])
    if not sub["topic_ids"] and not sub.get("source_ids"):
        raise SystemExit("Selected user has no raw-feed topics or sources configured")

    date_from, date_to = _date_range(args, sub["frequency_days"])
    digest = build_raw_feed_digest(
        sub["topic_ids"],
        date_from,
        date_to,
        user_id=user["id"],
        source_ids=sub.get("source_ids"),
    )
    if not digest.strip():
        raise SystemExit(f"No raw-feed items found for {date_from} to {date_to}")
    return user, date_from, date_to, digest


def _send_text(service, body, args):
    chunks = _split_by_utf8_bytes(body, args.max_bytes)
    for index, chunk in enumerate(chunks, start=1):
        prefix = f"[{index}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        service.notify(
            channel="wecom_webhook",
            title="",
            content=prefix + chunk,
        )
    return len(chunks)


def main():
    args = _parse_args()
    if args.list_users:
        _list_users()
        return

    user, date_from, date_to, digest = _build_digest(args)
    title = f"新增信息流日报测试\n{date_from} to {date_to}\n用户：{user['email']}"
    body = f"{title}\n\n{digest}"

    if args.format == "pdf":
        pdf_path = render_raw_feed_digest_pdf(
            body,
            filename_prefix=f"raw-feed-{date_from}-to-{date_to}",
        )
        if args.dry_run:
            print(f"raw_feed_digest_pdf_ready user={user['email']} range={date_from}..{date_to} pdf={pdf_path}")
            return
        service = NotificationService.from_config()
        service.notify_file(
            channel="wecom_webhook",
            file_path=pdf_path,
            title=f"新增信息流日报 PDF\n{date_from} to {date_to}",
        )
        print(f"wecom_raw_feed_pdf_sent user={user['email']} range={date_from}..{date_to} pdf={pdf_path}")
        return

    chunks = _split_by_utf8_bytes(body, args.max_bytes)
    if args.dry_run:
        print(f"raw_feed_digest_ready user={user['email']} range={date_from}..{date_to} chunks={len(chunks)}")
        print(chunks[0][:1000])
        return

    service = NotificationService.from_config()
    count = _send_text(service, body, args)
    print(f"wecom_raw_feed_sent user={user['email']} range={date_from}..{date_to} chunks={count}")


if __name__ == "__main__":
    main()
