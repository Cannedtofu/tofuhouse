"""
Database package for news_agent.

Sub-modules by domain:
  core        — connection management, schema init, migrations
  sources     — news source CRUD
  articles    — article CRUD, digest abstracts
  fetch_log   — fetch run history
  users       — user accounts, source follows, digest preferences
  digests     — AI digest cache, token usage
  transcripts — YouTube transcript jobs

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
)
from db.transcripts import (
    create_transcript_job,
    get_done_transcript_job,
    list_transcript_jobs,
    update_transcript_job,
    get_transcript_job,
    set_transcript_metadata,
    delete_transcript_job,
)

__all__ = [
    # core
    "get_conn", "init_db",
    # sources
    "seed_default_sources", "get_all_sources", "get_source_by_id",
    "upsert_source", "delete_source", "update_source_last_fetched",
    # articles
    "insert_article", "get_article_by_id", "get_unsummarized_articles",
    "update_summary", "get_articles", "delete_article",
    "get_digest_abstract", "update_digest_abstract",
    # fetch log
    "log_fetch_start", "log_fetch_finish", "close_open_fetch_logs", "get_fetch_log",
    # users
    "get_or_create_user", "get_user_by_id", "get_all_users", "get_users_due_for_digest",
    "get_followed_source_ids", "follow_source", "unfollow_source",
    "get_all_sources_with_follow_status", "set_user_follows",
    "update_user_digest_settings", "update_user_digest_last_sent",
    # digests
    "get_all_digests_with_meta", "get_digest_cache", "save_digest_cache",
    "log_token_usage", "get_token_usage_summary", "get_token_usage_by_user_week",
    # transcripts
    "create_transcript_job", "get_done_transcript_job", "list_transcript_jobs",
    "update_transcript_job", "get_transcript_job", "set_transcript_metadata",
    "delete_transcript_job",
]
