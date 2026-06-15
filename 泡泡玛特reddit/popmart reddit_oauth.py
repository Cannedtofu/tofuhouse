from __future__ import annotations

import requests
import requests.auth
import pandas as pd
import time
import datetime
from datetime import timezone
import os
import glob

# ========================= 配置区域 (Configuration) =========================
USER_NAME = "MasterTofu996"
USER_AGENT = f'python:popmart_key_words_project:v3.0 (by /u/{USER_NAME})'

# --- OAuth 认证（script 类型应用）---
# 注册步骤：https://www.reddit.com/prefs/apps → "create another app" → 选 "script"
# Redirect URI 填 http://localhost:8080（仅占位，实际不使用）
CLIENT_ID     = ""   # 应用名称下方的字符串（client_id）
CLIENT_SECRET = ""   # 应用的 secret
USER_PASSWORD = ""   # MasterTofu996 的账号密码

# --- 时间过滤 ---
# 关键词全局搜索：只保留 2026 年 1 月 1 日及之后的帖子
KEYWORD_MIN_DATE   = datetime.datetime(2026, 1, 1, tzinfo=timezone.utc)
# 专项子版块抓取：保留 2025 年 1 月 1 日及之后的帖子（覆盖 2025 + 2026）
SUBREDDIT_MIN_DATE = datetime.datetime(2025, 1, 1, tzinfo=timezone.utc)

# --- 关键词搜索列表 ---
KEYWORDS = [
    "popmart", "labubu", "popmart hirono", "popmart skullpanda",
    "popmart peach riot", "popmart twinkle twinkle", "popmart crybaby",
    "popmart molly", "popmart dimoo"
]

# --- 专项子版块列表（按 /new 全量抓取，直到达到日期下限）---
SUBREDDITS = [
    "SkullpandaArtDolls", "labubu", "CryBabyDolls", "hirono",
    "peachriot", "PopMartCollectors", "Dimoos", "TwinkleTwinkleCollect"
]

TEST_MODE = False
POSTS_PER_KEYWORD = 1 if TEST_MODE else 100   # 关键词搜索每个关键词最多抓 100 篇新帖

# --- 去重配置 ---
EXISTING_EXCEL_PATTERN = "popmart_v*.xlsx"

# --- 请求频率 ---
# OAuth 应用额度：600 次/10 分钟 = 100 次/分钟。
# 此处 3 秒延迟 ≈ 20 次/分钟，远低于上限，为评论详情抓取留出充足余量。
REQUEST_DELAY = 3.0    # 每次帖子详情请求之后的延迟（秒）
KEYWORD_PAUSE = 60.0   # 每个关键词/子版块完成后的强制休息（秒）
# ===========================================================================

# OAuth 令牌端点（始终在 www，不在 oauth 子域）
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
# 所有数据端点使用 oauth.reddit.com（正式 OAuth API，无需 .json 后缀）
_BASE = "https://oauth.reddit.com"


class PopmartScraper:

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.base_path = os.path.dirname(os.path.abspath(__file__))
        self.all_results: list[dict] = []
        self.scraped_post_ids: set[str] = set()
        self._token_expires_at: float = 0.0   # unix timestamp

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _authenticate(self) -> None:
        """获取或刷新 OAuth access token（password grant，script app）。"""
        resp = requests.post(
            _TOKEN_URL,
            auth=requests.auth.HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
            data={
                "grant_type": "password",
                "username":   USER_NAME,
                "password":   USER_PASSWORD,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(f"OAuth 认证失败: {data['error']}\n"
                               "请检查 CLIENT_ID / CLIENT_SECRET / USER_PASSWORD。")

        token      = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        self.session.headers.update({"Authorization": f"bearer {token}"})
        # 提前 60s 刷新，避免在请求中途令牌过期
        self._token_expires_at = time.time() + expires_in - 60
        print(f"✅ OAuth 认证成功（令牌有效期 {expires_in}s，"
              f"将在 {expires_in - 60}s 后自动刷新）")

    def _ensure_token_valid(self) -> None:
        """若令牌已过期或即将过期，静默刷新。"""
        if time.time() >= self._token_expires_at:
            print("🔄 OAuth 令牌过期，自动刷新...")
            self._authenticate()

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def load_existing_ids(self) -> set[str]:
        """扫描所有历史 Excel 文件，返回已有 ID 集合（帖子 + 评论）。"""
        existing_ids: set[str] = set()
        pattern = os.path.join(self.base_path, EXISTING_EXCEL_PATTERN)
        files   = sorted(glob.glob(pattern))

        if not files:
            print("ℹ️  未发现历史数据文件，将全量抓取。")
            return existing_ids

        for f in files:
            try:
                df  = pd.read_excel(f, usecols=["ID"])
                ids = df["ID"].dropna().astype(str).tolist()
                existing_ids.update(ids)
                print(f"   📂 {os.path.basename(f)}: 加载 {len(ids):,} 条 ID")
            except Exception as e:
                print(f"   ⚠️  读取 {os.path.basename(f)} 失败: {e}")

        print(f"✅ 历史 ID 合计 {len(existing_ids):,} 条，抓取时将跳过重复帖子。\n")
        return existing_ids

    def fetch_json(self, url: str, params: dict | None = None) -> dict | None:
        """
        执行一次 GET 请求并返回 JSON。
        - 请求前检查令牌有效性，必要时自动刷新
        - 读取 X-Ratelimit-* 响应头，在配额耗尽前主动减速
        - 401：令牌意外失效时刷新后重试一次
        - 429：遵守 Retry-After 头部
        """
        self._ensure_token_valid()

        try:
            resp = self.session.get(url, params=params, timeout=15)

            # 主动频率管控
            rem   = resp.headers.get("X-Ratelimit-Remaining")
            reset = resp.headers.get("X-Ratelimit-Reset")
            if rem is not None:
                try:
                    rem_val = float(rem)
                    if rem_val < 5:
                        wait = float(reset) + 2 if reset else 90
                        print(f"🛑 配额预警：剩余 {rem_val:.0f} 次，强制休眠 {wait:.0f}s...")
                        time.sleep(wait)
                    elif rem_val < 20:
                        time.sleep(1.5)
                except ValueError:
                    pass

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 401:
                # 令牌意外失效（例如密码已更改），刷新后重试一次
                print("⚠️  收到 401，刷新令牌后重试...")
                self._authenticate()
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 200:
                    return resp.json()
                print(f"   ❌ 重试后仍失败 (HTTP {resp.status_code})。")

            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 90))
                print(f"⚠️  触发 429，遵守 Retry-After 休眠 {retry_after + 5}s...")
                time.sleep(retry_after + 5)

            elif resp.status_code == 403:
                print(f"   ❌ 403 拒绝访问（子版块可能为私密或已限制）: {url}")

            elif resp.status_code == 404:
                print(f"   ❌ 404 未找到: {url}")

            else:
                print(f"   ⚠️  HTTP {resp.status_code}: {url}")

            return None

        except Exception as e:
            print(f"   ❌ 网络异常: {e}")
            return None

    def flatten_comments(self, children: list, post_title: str,
                         keyword: str, total_comments: int,
                         link_id: str, level: int = 1) -> list:
        """递归展平多层评论树，返回扁平化的记录列表。"""
        flat: list[dict] = []
        for child in children:
            if child.get("kind") != "t1":
                continue
            data = child.get("data", {})
            flat.append({
                "ID":           data.get("name"),
                "父级ID":       data.get("parent_id"),
                "发布时间":     datetime.datetime.fromtimestamp(
                                    data.get("created_utc", 0), tz=timezone.utc
                                ).strftime("%Y-%m-%d %H:%M:%S"),
                "搜索关键词":   keyword,
                "数据类型":     "评论 (Comment)",
                "层级":         f"第 {level} 层",
                "帖子总评论数": total_comments,
                "内容正文":     data.get("body", ""),
                "作者":         data.get("author", "[deleted]"),
                "热度(Score)":  data.get("score", 0),
                "所属标题":     post_title,
            })
            replies = data.get("replies")
            if isinstance(replies, dict):
                inner = replies.get("data", {}).get("children", [])
                flat.extend(self.flatten_comments(
                    inner, post_title, keyword, total_comments, link_id, level + 1
                ))
        return flat

    def fetch_post_with_comments(
        self,
        item: dict,
        keyword: str,
        existing_ids: set[str],
    ) -> tuple[list, str | None]:
        """
        抓取单篇帖子及其所有评论。
        若帖子 ID 已在历史数据或本轮已抓集合中，跳过并返回 ([], None)。
        """
        post_id = item.get("name")      # e.g. t3_abc123
        if not post_id:
            return [], None

        if post_id in existing_ids or post_id in self.scraped_post_ids:
            print(f"      ⏭️  已存在，跳过: {item.get('title', '')[:50]}")
            return [], None

        title          = item.get("title", "")
        total_comments = item.get("num_comments", 0)
        records: list[dict] = []

        records.append({
            "ID":           post_id,
            "父级ID":       "ROOT",
            "发布时间":     datetime.datetime.fromtimestamp(
                                item.get("created_utc", 0), tz=timezone.utc
                            ).strftime("%Y-%m-%d %H:%M:%S"),
            "搜索关键词":   keyword,
            "数据类型":     "帖子 (Post)",
            "层级":         "0",
            "帖子总评论数": total_comments,
            "内容正文":     item.get("selftext", ""),
            "作者":         item.get("author"),
            "热度(Score)":  item.get("score"),
            "所属标题":     title,
        })

        # permalink 形如 /r/labubu/comments/abc123/title/
        permalink = item.get("permalink", "")
        detail = self.fetch_json(f"{_BASE}{permalink}")
        if detail and len(detail) > 1:
            raw_comments = detail[1]["data"].get("children", [])
            records.extend(self.flatten_comments(
                raw_comments, title, keyword, total_comments, post_id
            ))

        return records, post_id

    def save_data(self, tag: str) -> None:
        """增量保存当前所有结果到以今日日期命名的 Excel 文件。"""
        if not self.all_results:
            return
        df       = pd.DataFrame(self.all_results).drop_duplicates(subset=["ID"])
        filename = f"popmart_v3.0_{datetime.date.today()}.xlsx"
        path     = os.path.join(self.base_path, filename)
        try:
            df.to_excel(path, index=False)
            print(f"   💾 [保存] {tag} | 当前累计 {len(df):,} 行 → {filename}")
        except Exception as e:
            print(f"   ❌ 保存失败: {e}")

    # ------------------------------------------------------------------
    # 阶段 1：关键词全局搜索
    # ------------------------------------------------------------------

    def scrape_keywords(self, existing_ids: set[str]) -> None:
        """
        使用 Reddit 全局搜索逐关键词抓取帖子。
        - sort=new：新→旧，遇到早于 KEYWORD_MIN_DATE 的帖子立即停止
        - 每关键词最多保留 POSTS_PER_KEYWORD 篇去重后的新帖
        """
        run_keywords = [KEYWORDS[0]] if TEST_MODE else KEYWORDS
        print(f"\n{'='*65}")
        print(f"📌 阶段 1：关键词搜索  ({len(run_keywords)} 个关键词，"
              f"每个最多 {POSTS_PER_KEYWORD} 篇新帖)")
        print(f"   日期下限：{KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} (UTC)")
        print(f"{'='*65}")

        for kw in run_keywords:
            print(f"\n🔍 关键词: [{kw}]")
            new_posts = 0
            after     = None
            stop      = False

            while new_posts < POSTS_PER_KEYWORD and not stop:
                params = {
                    "q":     kw,
                    "sort":  "new",
                    "t":     "all",
                    "limit": 100,
                    "after": after,
                }
                data = self.fetch_json(f"{_BASE}/search", params)
                if not data:
                    break

                children = data["data"].get("children", [])
                if not children:
                    break

                for p in children:
                    if new_posts >= POSTS_PER_KEYWORD:
                        break

                    item    = p["data"]
                    post_ts = datetime.datetime.fromtimestamp(
                        item.get("created_utc", 0), tz=timezone.utc
                    )

                    if post_ts < KEYWORD_MIN_DATE:
                        print(f"   📅 到达日期边界 ({post_ts.strftime('%Y-%m-%d')})，停止。")
                        stop = True
                        break

                    print(f"   [{new_posts + 1}] {item.get('title', '')[:55]}...")
                    records, post_id = self.fetch_post_with_comments(
                        item, kw, existing_ids
                    )

                    if post_id:
                        self.all_results.extend(records)
                        self.scraped_post_ids.add(post_id)
                        new_posts += 1
                        time.sleep(REQUEST_DELAY)

                after = data["data"].get("after")
                if not after or TEST_MODE:
                    break

                time.sleep(REQUEST_DELAY)

            print(f"   ✅ [{kw}] 完成，本次新增 {new_posts} 篇。")
            self.save_data(f"kw_{kw[:20]}")

            if kw != run_keywords[-1]:
                print(f"   ⏳ 关键词间休眠 {KEYWORD_PAUSE:.0f}s...")
                time.sleep(KEYWORD_PAUSE)

    # ------------------------------------------------------------------
    # 阶段 2：专项子版块全量抓取
    # ------------------------------------------------------------------

    def scrape_subreddits(self, existing_ids: set[str]) -> None:
        """
        逐子版块通过 /new 端点分页抓取全量帖子，直至遇到早于 SUBREDDIT_MIN_DATE 的帖子。
        子版块内帖子数量不设上限；每页 100 条，自动翻页。
        """
        run_subs = [SUBREDDITS[0]] if TEST_MODE else SUBREDDITS
        print(f"\n{'='*65}")
        print(f"📌 阶段 2：子版块全量抓取  ({len(run_subs)} 个子版块)")
        print(f"   日期下限：{SUBREDDIT_MIN_DATE.strftime('%Y-%m-%d')} (UTC)")
        print(f"{'='*65}")

        for sub in run_subs:
            print(f"\n🏠 子版块: [r/{sub}]")
            new_posts = 0
            after     = None
            stop      = False
            page      = 0

            while not stop:
                page  += 1
                params = {"limit": 100, "after": after}
                data   = self.fetch_json(f"{_BASE}/r/{sub}/new", params)

                if not data:
                    print(f"   ⚠️  第 {page} 页请求失败，停止。")
                    break

                children = data["data"].get("children", [])
                if not children:
                    print(f"   ℹ️  第 {page} 页无数据，停止。")
                    break

                oldest_on_page = datetime.datetime.fromtimestamp(
                    children[-1]["data"].get("created_utc", 0), tz=timezone.utc
                )
                print(f"   📄 第 {page} 页 ({len(children)} 篇帖子，"
                      f"最早 {oldest_on_page.strftime('%Y-%m-%d')})...")

                for p in children:
                    item    = p["data"]
                    post_ts = datetime.datetime.fromtimestamp(
                        item.get("created_utc", 0), tz=timezone.utc
                    )

                    if post_ts < SUBREDDIT_MIN_DATE:
                        print(f"   📅 到达日期边界 ({post_ts.strftime('%Y-%m-%d')})，停止分页。")
                        stop = True
                        break

                    records, post_id = self.fetch_post_with_comments(
                        item, f"r/{sub}", existing_ids
                    )

                    if post_id:
                        self.all_results.extend(records)
                        self.scraped_post_ids.add(post_id)
                        new_posts += 1
                        time.sleep(REQUEST_DELAY)

                after = data["data"].get("after")
                if not after:
                    print(f"   ℹ️  已到最后一页，停止。")
                    break

                time.sleep(REQUEST_DELAY)

            print(f"   ✅ [r/{sub}] 完成，本次新增 {new_posts} 篇。")
            self.save_data(f"sub_{sub[:20]}")

            if sub != run_subs[-1]:
                print(f"   ⏳ 子版块间休眠 {KEYWORD_PAUSE:.0f}s...")
                time.sleep(KEYWORD_PAUSE)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        print("🚀 Popmart Reddit 数据采集 v3.0 (OAuth)")
        print(f"   关键词日期下限：{KEYWORD_MIN_DATE.strftime('%Y-%m-%d')} (2026+)")
        print(f"   子版块日期下限：{SUBREDDIT_MIN_DATE.strftime('%Y-%m-%d')} (2025+)\n")

        # OAuth 认证（整个 run 期间自动续期）
        self._authenticate()

        # 加载历史 ID，用于全局去重
        existing_ids = self.load_existing_ids()

        # 阶段 1：关键词搜索
        self.scrape_keywords(existing_ids)

        # 阶段 2：子版块全量抓取
        self.scrape_subreddits(existing_ids)

        # 最终保存
        self.save_data("FINAL")

        unique_new = len(set(r["ID"] for r in self.all_results))
        print(f"\n🎉 全部完成！本次共新增 {unique_new:,} 条唯一记录。")


if __name__ == "__main__":
    PopmartScraper().run()
