import pandas as pd
import datetime
import sys
import os
import io
import base64

WEEKDAY_ZH = {0: '周一', 1: '周二', 2: '周三', 3: '周四', 4: '周五', 5: '周六', 6: '周日'}

_TH = ('padding:8px 14px;border:1px solid #d0d0d0;background:#f0f0f0;'
       'text-align:left;white-space:nowrap;font-weight:bold;')
_TD = 'padding:7px 14px;border:1px solid #d0d0d0;text-align:left;white-space:nowrap;'
_TD_BOLD = ('padding:7px 14px;border:1px solid #d0d0d0;text-align:left;'
            'white-space:nowrap;font-weight:bold;background:#fafafa;')
_TABLE = 'border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;margin-bottom:4px;'


def _html_table(rows, headers, highlight_last=False):
    html = f'<table style="{_TABLE}"><thead><tr>'
    html += ''.join(f'<th style="{_TH}">{h}</th>' for h in headers)
    html += '</tr></thead><tbody>\n'
    for i, row in enumerate(rows):
        td = _TD_BOLD if (highlight_last and i == len(rows) - 1) else _TD
        html += '<tr>' + ''.join(f'<td style="{td}">{row.get(h, "")}</td>' for h in headers) + '</tr>\n'
    html += '</tbody></table>\n'
    return html


def _wow_city_table(df, target_date, last_week_date):
    """City-level WoW comparison: today vs 7 days ago, comparable stores only."""
    df_this = df[df['Date'] == target_date].copy()
    df_last = df[df['Date'] == last_week_date].copy()

    if df_this.empty:
        return f'<p style="color:#c00;">警告：未找到 {target_date} 的数据。</p>'
    if df_last.empty:
        return f'<p style="color:#c00;">上周 ({last_week_date}) 数据缺失，WoW 对比无法计算。</p>'

    common = set(df_this['Store Name'].unique()) & set(df_last['Store Name'].unique())
    if not common:
        return '<p style="color:#c00;">未找到两个日期均存在的门店。</p>'

    this_agg = (df_this[df_this['Store Name'].isin(common)]
                .groupby(['City', 'Store Name'])['Cup Count'].mean().reset_index())
    last_agg = (df_last[df_last['Store Name'].isin(common)]
                .groupby(['City', 'Store Name'])['Cup Count'].mean().reset_index())

    merged = pd.merge(last_agg, this_agg, on=['City', 'Store Name'], suffixes=('_L', '_T'))
    city = merged.groupby('City').agg(
        last=('Cup Count_L', 'sum'),
        this=('Cup Count_T', 'sum'),
        n=('Store Name', 'count')
    ).reset_index()
    city['wow'] = ((city['this'] - city['last']) / city['last'] * 100).round(2)

    rows = []
    for _, r in city.iterrows():
        rows.append({
            '城市': r['City'],
            '上周杯数': int(r['last']),
            '本周杯数': int(r['this']),
            '周同比 %': f"{r['wow']:+.2f}%",
            '对比门店数': int(r['n'])
        })

    total_last = int(city['last'].sum())
    total_this = int(city['this'].sum())
    total_wow = ((total_this - total_last) / total_last * 100) if total_last else 0
    rows.append({
        '城市': '总计',
        '上周杯数': total_last,
        '本周杯数': total_this,
        '周同比 %': f"{total_wow:+.2f}%",
        '对比门店数': int(city['n'].sum())
    })

    headers = ['城市', '上周杯数', '本周杯数', '周同比 %', '对比门店数']
    return _html_table(rows, headers, highlight_last=True)


def _same_weekday_trend(df, target_date):
    """
    For each historical same-weekday date, compare against today's data.
    Common stores = intersection of that date and today.
    Rows are sorted oldest-first; today's row is appended last and highlighted.
    """
    weekday = target_date.weekday()
    all_days = sorted(d for d in df['Date'].dropna().unique() if d.weekday() == weekday)
    hist_days = [d for d in all_days if d != target_date]

    df_today = df[df['Date'] == target_date]
    if df_today.empty:
        return f'<p style="color:#c00;">警告：未找到 {target_date} 的数据。</p>'
    if not hist_days:
        return '<p>历史同工作日数据不足，无法计算趋势。</p>'

    today_stores = set(df_today['Store Name'].unique())
    cups_today_all = int(df_today.groupby(['City', 'Store Name'])['Cup Count'].mean().sum())

    rows = []
    for hist_dt in hist_days:
        df_hist = df[df['Date'] == hist_dt]
        common = today_stores & set(df_hist['Store Name'].unique())
        if not common:
            continue

        cups_today = int(df_today[df_today['Store Name'].isin(common)]
                         .groupby(['City', 'Store Name'])['Cup Count'].mean().sum())
        cups_hist = int(df_hist[df_hist['Store Name'].isin(common)]
                        .groupby(['City', 'Store Name'])['Cup Count'].mean().sum())
        wow = ((cups_today - cups_hist) / cups_hist * 100) if cups_hist else 0

        rows.append({
            '历史日期': str(hist_dt),
            '历史杯数': cups_hist,
            '今日杯数': cups_today,
            '变化 %': f"{wow:+.2f}%",
            '对比门店数': len(common),
        })

    if not rows:
        return '<p>历史同工作日数据不足，无法计算趋势。</p>'

    rows.append({
        '历史日期': f'今日合计 ({target_date})',
        '历史杯数': '—',
        '今日杯数': cups_today_all,
        '变化 %': '—',
        '对比门店数': len(today_stores),
    })

    headers = ['历史日期', '历史杯数', '今日杯数', '变化 %', '对比门店数']
    return _html_table(rows, headers, highlight_last=True)


def analyze_stores(file_path, input_date_str=None):
    if not os.path.exists(file_path):
        return f'<p>错误：未找到文件 "{file_path}"。</p>'
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        return f'<p>读取 Excel 文件时出错：{e}</p>'

    df['Date'] = pd.to_datetime(df['Date']).dt.date
    df['Cup Count'] = pd.to_numeric(df['Cup Count'], errors='coerce').fillna(0)

    if input_date_str:
        try:
            target_date = datetime.datetime.strptime(input_date_str, "%Y-%m-%d").date()
        except ValueError:
            return f'<p>日期格式无效："{input_date_str}"。</p>'
    else:
        target_date = datetime.date.today()

    last_week_date = target_date - datetime.timedelta(days=7)
    weekday_zh = WEEKDAY_ZH[target_date.weekday()]

    html = ''

    html += f'<h3 style="margin-bottom:6px;">周同比分析 (WoW)&nbsp;&nbsp;{target_date} vs {last_week_date}</h3>\n'
    html += _wow_city_table(df, target_date, last_week_date)

    html += f'<h3 style="margin-top:24px;margin-bottom:6px;">历史{weekday_zh}同比趋势（同店可比）</h3>\n'
    html += _same_weekday_trend(df, target_date)

    return html


def cups_per_store_chart(file_path):
    """
    Returns a base64-encoded PNG of cups-per-store over time.
    Only includes days where total store count > 799.
    Days with 0 cups/stores are dropped; remaining points are connected directly.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    if not os.path.exists(file_path):
        return None
    try:
        df = pd.read_excel(file_path)
    except Exception:
        return None

    df['Date'] = pd.to_datetime(df['Date']).dt.date
    df['Cup Count'] = pd.to_numeric(df['Cup Count'], errors='coerce').fillna(0)

    daily = df.groupby('Date').agg(
        store_count=('Store Name', 'count'),
        total_cups=('Cup Count', 'sum')
    ).reset_index()

    daily = daily[daily['store_count'] > 799].copy()
    daily = daily[daily['total_cups'] > 0].copy()
    daily['cups_per_store'] = daily['total_cups'] / daily['store_count']
    daily = daily.sort_values('Date')

    if len(daily) < 2:
        return None

    dates = [datetime.datetime.combine(d, datetime.time()) for d in daily['Date']]
    values = daily['cups_per_store'].tolist()

    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'SimSun', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False

    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.plot(dates, values, marker='o', markersize=4, linewidth=1.8,
            color='#2c7bb6', markerfacecolor='#2c7bb6')

    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    fig.autofmt_xdate(rotation=30, ha='right')

    ax.set_ylabel('杯数 / 门店', fontsize=11)
    ax.set_title('每日人均杯数趋势（门店数 > 799）', fontsize=13, pad=10)
    ax.yaxis.grid(True, linestyle='--', alpha=0.5)
    ax.set_axisbelow(True)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


if __name__ == "__main__":
    FILE_PATH = "multi_city_stores.xlsx"
    target_date_input = sys.argv[1] if len(sys.argv) > 1 else None
    print(analyze_stores(FILE_PATH, target_date_input))
