"""Quick validation test: 3 posts from keyword search + 3 from subreddit."""
from playwright.sync_api import sync_playwright
import time, datetime, pandas as pd
from datetime import timezone

BASE_URL   = "https://old.reddit.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
MIN_TS = int(datetime.datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp())
MAX_TEST_POSTS = 3

_SEARCH_EXTRACT_JS = """
() => {
    const results = [];
    const items = document.querySelectorAll('.search-result-link[data-fullname]');
    items.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname.startsWith('t3_')) return;
        const titleEl = el.querySelector('.search-title');
        const title   = titleEl ? titleEl.innerText.trim() : '';
        let permalink = '';
        if (titleEl && titleEl.href) {
            try { permalink = new URL(titleEl.href).pathname; } catch(e) {}
        }
        const timeEl = el.querySelector('time');
        const dt     = timeEl ? timeEl.getAttribute('datetime') : null;
        const createdUtc = dt ? Date.parse(dt) / 1000 : 0;
        const scoreEl    = el.querySelector('.search-score');
        const score      = scoreEl ? (parseInt(scoreEl.textContent) || 0) : 0;
        const commentsEl = el.querySelector('.search-comments');
        const numComments = commentsEl ? (parseInt(commentsEl.textContent) || 0) : 0;
        const authorEl   = el.querySelector('.search-author .author');
        const author     = authorEl ? authorEl.textContent.trim() : '[deleted]';
        results.push({ fullname, id: fullname.replace('t3_',''), created_utc: createdUtc,
                       author, score, num_comments: numComments, permalink, title });
    });
    return results;
}
"""

_LISTING_EXTRACT_JS = """
() => {
    const results = [];
    const things = document.querySelectorAll('.thing[data-type="link"][data-fullname]');
    things.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname.startsWith('t3_')) return;
        const titleEl = el.querySelector('a.title');
        const tsMs    = parseFloat(el.getAttribute('data-timestamp') || '0');
        results.push({
            fullname, id: fullname.replace('t3_',''),
            created_utc:  tsMs / 1000,
            author:       el.getAttribute('data-author') || '[deleted]',
            score:        parseInt(el.getAttribute('data-score') || '0') || 0,
            num_comments: parseInt(el.getAttribute('data-comments-count') || '0'),
            permalink:    el.getAttribute('data-permalink') || '',
            title:        titleEl ? titleEl.innerText.trim() : '',
        });
    });
    return results;
}
"""

_COMMENT_EXTRACT_JS = """
() => {
    const results = [];
    const things = document.querySelectorAll('.commentarea [data-fullname^="t1_"]');
    things.forEach(el => {
        const fullname = el.getAttribute('data-fullname') || '';
        if (!fullname) return;
        const timeEl = el.querySelector('time');
        const dt     = timeEl ? timeEl.getAttribute('datetime') : null;
        const createdUtc = dt ? Date.parse(dt) / 1000 : 0;
        const scoreEl = el.querySelector('.score');
        const score   = scoreEl ? (parseInt(scoreEl.getAttribute('title') || '0') || 0) : 0;
        const bodyEl  = el.querySelector('.usertext-body .md');
        const body    = bodyEl ? bodyEl.innerText.trim() : '';
        let parentFullname = '';
        let p = el.parentElement;
        while (p) {
            if (p.classList.contains('commentarea')) {
                const postEl = document.querySelector('.thing[data-type="link"][data-fullname]');
                parentFullname = postEl ? postEl.getAttribute('data-fullname') : '';
                break;
            }
            const fn = p.getAttribute && p.getAttribute('data-fullname');
            if (fn && fn !== fullname) { parentFullname = fn; break; }
            p = p.parentElement;
        }
        results.push({ fullname, parent_fullname: parentFullname,
                       author: el.getAttribute('data-author') || '[deleted]',
                       created_utc: createdUtc, score, body });
    });
    return results;
}
"""

def fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def safe(s, maxlen=90):
    """Truncate and strip non-ASCII so Windows GBK terminal won't crash."""
    return s[:maxlen].encode("ascii", "replace").decode("ascii")

def fetch_post_page(page, post, keyword, all_records):
    permalink = post.get("permalink", "")
    if not permalink:
        print("   [WARN] No permalink - skip")
        return
    url = BASE_URL + permalink + "?limit=500"
    print(f"   Fetching post page: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)
    except Exception as e:
        print(f"   [ERR] {e}")
        return

    body = ""
    try:
        body_el = page.query_selector(".link .usertext-body .md, .expando .usertext-body .md")
        if body_el:
            body = body_el.inner_text().strip()
    except Exception:
        pass

    comments = []
    try:
        comments = page.evaluate(_COMMENT_EXTRACT_JS) or []
    except Exception as e:
        print(f"   [WARN] Comment JS error: {e}")

    print(f"   -> {len(comments)} comment(s) fetched")
    if body:
        print(f"   -> Post body: {safe(body)}...")

    all_records.append({
        "ID": post["fullname"], "Parent_ID": "ROOT",
        "Posted_Time": fmt_ts(post["created_utc"]),
        "Keyword": keyword, "Data_Type": "Post", "Level": 0,
        "Total_Comments": post["num_comments"],
        "Body": body, "Author": post["author"],
        "Score": post["score"], "Post_Title": post["title"],
    })
    for c in comments:
        parent = c.get("parent_fullname", "")
        level  = 1 if parent.startswith("t3_") else 2
        all_records.append({
            "ID": c["fullname"], "Parent_ID": parent,
            "Posted_Time": fmt_ts(c["created_utc"]),
            "Keyword": keyword, "Data_Type": "Comment", "Level": level,
            "Total_Comments": post["num_comments"],
            "Body": c["body"], "Author": c["author"],
            "Score": c["score"], "Post_Title": post["title"],
        })

all_records = []

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 768})
    ctx.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico,woff,woff2}", lambda r: r.abort())
    page = ctx.new_page()

    # Warmup
    page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    print(f"[WARMUP] {page.title()}")

    # ============================================================
    # TEST A: keyword search for 'labubu'
    # ============================================================
    print()
    print("=" * 60)
    print(f"TEST A: keyword [labubu] - first {MAX_TEST_POSTS} posts")
    print("=" * 60)
    page.goto(BASE_URL + "/search/?q=labubu&sort=new&t=all&type=link",
              wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.5)
    posts = page.evaluate(_SEARCH_EXTRACT_JS) or []
    print(f"Search page returned {len(posts)} posts")

    count = 0
    for post in posts:
        if count >= MAX_TEST_POSTS:
            break
        if post["created_utc"] < MIN_TS:
            print(f"  [DATE SKIP] {fmt_ts(post['created_utc'])} - before 2025")
            continue
        count += 1
        print(f"\n  [{count}] {post['fullname']}")
        print(f"       Title:    {safe(post['title'])}")
        print(f"       Date:     {fmt_ts(post['created_utc'])}")
        print(f"       Author:   {post['author']}")
        print(f"       Score:    {post['score']}  |  Comments: {post['num_comments']}")
        fetch_post_page(page, post, "labubu", all_records)
        time.sleep(3.5)

    # ============================================================
    # TEST B: subreddit r/PopMartCollectors
    # ============================================================
    print()
    print("=" * 60)
    print(f"TEST B: subreddit r/PopMartCollectors - first {MAX_TEST_POSTS} posts")
    print("=" * 60)
    page.goto(BASE_URL + "/r/PopMartCollectors/new/",
              wait_until="domcontentloaded", timeout=30000)
    time.sleep(3.5)
    posts = page.evaluate(_LISTING_EXTRACT_JS) or []
    print(f"Subreddit /new/ returned {len(posts)} posts")

    count = 0
    for post in posts:
        if count >= MAX_TEST_POSTS:
            break
        if post["created_utc"] < MIN_TS:
            print(f"  [DATE SKIP] {fmt_ts(post['created_utc'])} - before 2025")
            continue
        count += 1
        print(f"\n  [{count}] {post['fullname']}")
        print(f"       Title:    {safe(post['title'])}")
        print(f"       Date:     {fmt_ts(post['created_utc'])}")
        print(f"       Author:   {post['author']}")
        print(f"       Score:    {post['score']}  |  Comments: {post['num_comments']}")
        fetch_post_page(page, post, "r/PopMartCollectors", all_records)
        time.sleep(3.5)

    browser.close()

# ============================================================
# Summary + Excel
# ============================================================
print()
print("=" * 60)
df = pd.DataFrame(all_records)
posts_df    = df[df["Data_Type"] == "Post"]
comments_df = df[df["Data_Type"] == "Comment"]

print(f"TOTAL RECORDS : {len(df)}")
print(f"  Posts       : {len(posts_df)}")
print(f"  Comments    : {len(comments_df)}")
print(f"  Level-1 comments: {len(df[df['Level']==1])}")
print(f"  Level-2 comments: {len(df[df['Level']==2])}")
print()
print("All records:")
display_df = df[["ID","Data_Type","Level","Posted_Time","Author","Score","Post_Title"]].copy()
display_df["Post_Title"] = display_df["Post_Title"].apply(lambda x: safe(str(x), 50))
print(display_df.to_string(index=False))

# Check parent-child integrity
print()
print("Parent-child spot check:")
for _, row in df[df["Data_Type"] == "Comment"].head(3).iterrows():
    parent_exists = row["Parent_ID"] in df["ID"].values
    print(f"  {row['ID']} -> parent={row['Parent_ID']} "
          f"| parent in dataset: {parent_exists} | level={row['Level']} "
          f"| body: {safe(row['Body'], 60)}")

df.to_excel("test_playwright_output.xlsx", index=False)
print()
print("[SAVED] test_playwright_output.xlsx")
