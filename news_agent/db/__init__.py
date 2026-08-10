"""
Database package for news_agent.

Sub-modules by domain:
  core        - connection management, schema init, migrations
  sources     - news source CRUD
  articles    - article CRUD, digest abstracts
  fetch_log   - fetch run history
  users       - user accounts, source follows, digest preferences
  digests     - AI digest cache, token usage
  raw_feeds   - raw feed digest subscriptions
  transcripts - YouTube transcript jobs
  topics      - tracked topics and topic items

All public functions are re-exported here so existing code using
`import db; db.some_function()` continues to work unchanged.
"""

from db.core import get_conn, init_db
from db.sources import (
    seed_default_sources,
    get_all_sources,
    get_source_by_id,
    upsert_source,
    delete_source,
    update_source_last_fetched,
)
from db.articles import (
    insert_article,
    get_article_by_id,
    get_unsummarized_articles,
    update_summary,
    get_articles,
    delete_article,
    get_digest_abstract,
    update_digest_abstract,
    update_article_translation,
)
from db.fetch_log import (
    log_fetch_start,
    log_fetch_finish,
    close_open_fetch_logs,
    get_fetch_log,
)
from db.users import (
    get_or_create_user,
    get_user_by_id,
    get_all_users,
    get_users_due_for_digest,
    get_followed_source_ids,
    follow_source,
    unfollow_source,
    get_all_sources_with_follow_status,
    set_user_follows,
    update_user_digest_settings,
    update_user_digest_last_sent,
)
from db.digests import (
    get_all_digests_with_meta,
    get_digest_cache,
    save_digest_cache,
    log_token_usage,
    get_token_usage_summary,
    get_token_usage_by_user_week,
    get_digest_presets,
    get_digest_presets_for_users,
    get_digest_preset,
    get_digest_preset_for_admin,
    create_digest_preset,
    update_digest_preset,
    update_preset_email_settings,
    update_preset_source_ids,
    delete_digest_preset,
    get_presets_due_for_email,
    update_preset_last_sent,
)
from db.raw_feeds import (
    get_raw_feed_subscription,
    update_raw_feed_subscription,
    get_raw_feed_subscriptions_due,
    get_raw_feed_subscriptions_for_users,
    update_raw_feed_subscription_last_sent,
)
from db.transcripts import (
    create_transcript_job,
    get_done_transcript_job,
    list_transcript_jobs,
    update_transcript_job,
    get_transcript_job,
    set_transcript_metadata,
    update_transcript_title,
    delete_transcript_job,
    clear_transcript_summary,
)
from db.topics import (
    create_topic,
    update_topic,
    get_all_topics,
    get_topic_by_id,
    delete_topic,
    update_topic_last_fetched,
    get_followed_topic_ids,
    follow_topic,
    unfollow_topic,
    set_user_topic_follows,
    get_all_topics_with_follow_status,
    get_topic_item_by_id,
    delete_topic_item,
    upsert_topic_item,
    get_topic_item_sources,
    get_topic_item_sources_bulk,
    get_topic_feed_items,
)
from db.gpu_prices import (
    upsert_gpu_price_data,
    get_gpu_price_data,
    get_all_gpu_price_data,
    get_gpu_price_last_updated,
)
from db.popmart_youtube import (
    upsert_popmart_youtube_snapshot,
    get_latest_popmart_youtube_videos,
    get_all_popmart_youtube_videos,
    get_latest_popmart_youtube_snapshot_at,
    get_popmart_youtube_weekly_summary,
    build_popmart_youtube_csv,
)
from db.script_reports import (
    upsert_script_report,
    get_all_script_reports,
    upsert_script_file,
    get_script_file,
    get_scripts_with_files,
    get_script_file_keys,
    get_panel_access,
    set_panel_access,
    delete_script_data,
)

__all__ = [
    "get_conn", "init_db",
    "seed_default_sources", "get_all_sources", "get_source_by_id",
    "upsert_source", "delete_source", "update_source_last_fetched",
    "insert_article", "get_article_by_id", "get_unsummarized_articles",
    "update_summary", "get_articles", "delete_article",
    "get_digest_abstract", "update_digest_abstract", "update_article_translation",
    "log_fetch_start", "log_fetch_finish", "close_open_fetch_logs", "get_fetch_log",
    "get_or_create_user", "get_user_by_id", "get_all_users", "get_users_due_for_digest",
    "get_followed_source_ids", "follow_source", "unfollow_source",
    "get_all_sources_with_follow_status", "set_user_follows",
    "update_user_digest_settings", "update_user_digest_last_sent",
    "get_all_digests_with_meta", "get_digest_cache", "save_digest_cache",
    "log_token_usage", "get_token_usage_summary", "get_token_usage_by_user_week",
    "get_digest_presets", "get_digest_presets_for_users", "get_digest_preset",
    "create_digest_preset", "update_digest_preset", "update_preset_email_settings",
    "update_preset_source_ids", "delete_digest_preset",
    "get_presets_due_for_email", "update_preset_last_sent",
    "get_raw_feed_subscription", "update_raw_feed_subscription",
    "get_raw_feed_subscriptions_due", "get_raw_feed_subscriptions_for_users",
    "update_raw_feed_subscription_last_sent",
    "create_transcript_job", "get_done_transcript_job", "list_transcript_jobs",
    "update_transcript_job", "get_transcript_job", "set_transcript_metadata",
    "update_transcript_title", "delete_transcript_job", "clear_transcript_summary",
    "create_topic", "update_topic", "get_all_topics", "get_topic_by_id", "delete_topic",
    "update_topic_last_fetched", "get_followed_topic_ids", "follow_topic", "unfollow_topic",
    "set_user_topic_follows", "get_all_topics_with_follow_status", "get_topic_item_by_id",
    "delete_topic_item", "upsert_topic_item", "get_topic_item_sources",
    "get_topic_item_sources_bulk", "get_topic_feed_items",
    "upsert_gpu_price_data", "get_gpu_price_data",
    "get_all_gpu_price_data", "get_gpu_price_last_updated",
    "upsert_script_report", "get_all_script_reports",
    "upsert_script_file", "get_script_file", "get_scripts_with_files", "get_script_file_keys",
    "get_panel_access", "set_panel_access", "delete_script_data",
    "upsert_popmart_youtube_snapshot", "get_latest_popmart_youtube_videos",
    "get_all_popmart_youtube_videos", "get_latest_popmart_youtube_snapshot_at",
    "get_popmart_youtube_weekly_summary", "build_popmart_youtube_csv",
]
