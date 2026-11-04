#!/usr/bin/env python3
"""外資持股占上市總市值（M10）：回補 / 每日增量更新 data/foreign-holding.json。

來源：TWSE MI_QFIIS（外資及陸資持股統計）+ MI_INDEX（每日收盤行情）。
用 curl 抓取（TWSE 憑證缺 Subject Key Identifier，Python 3.13+ 的嚴格 SSL 會擋）。
日期清單取自 data/taiex.json 的交易日。

用法：
  python3 scripts/foreign_holding.py            # 補齊缺的交易日（最近 250 日內）
  python3 scripts/foreign_holding.py --days 60  # 只看最近 60 個交易日
  python3 scripts/foreign_holding.py --since 2021-11-01  # 回補到指定日期（由新到舊）
"""
import argparse, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'foreign-holding.json')
TAIEX = os.path.join(ROOT, 'data', 'taiex.json')
BASE = 'https://www.twse.com.tw/rwd/zh'
SLEEP = 4  # TWSE 約 3 次/5 秒就會封鎖


def fetch(url, tries=4):
    for i in range(tries):
        try:
            r = subprocess.run(['curl', '-s', '--max-time', '60', '-A', 'Mozilla/5.0', url],
                               capture_output=True, timeout=90)
            if r.returncode == 0 and r.stdout.strip().startswith(b'{'):
                return json.loads(r.stdout.decode('utf-8'))
        except Exception:
            pass
        time.sleep(SLEEP * (i + 2))
    return None


def num(s):
    try:
        return float(str(s).replace(',', ''))
    except ValueError:
        return None


def compute(date):
    ymd = date.replace('-', '')
    q = fetch(f'{BASE}/fund/MI_QFIIS?date={ymd}&selectType=ALLBUT0999&response=json')
    time.sleep(SLEEP)
    m = fetch(f'{BASE}/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json')
    time.sleep(SLEEP)
    if not q or q.get('stat') != 'OK' or not q.get('data'):
        return None
    if not m or m.get('stat') != 'OK':
        return None

    tbl = next((t for t in m.get('tables', []) if '每日收盤行情' in (t.get('title') or '')), None)
    if not tbl or not tbl.get('data'):
        return None
    f = tbl['fields']
    ic, ip = f.index('證券代號'), f.index('收盤價')
    px = {r[ic]: num(r[ip]) for r in tbl['data']}

    qf = q['fields']
    i_iss = qf.index('發行股數')
    i_fh = qf.index('全體外資及陸資持有股數')
    i_pct = qf.index('全體外資及陸資持股比率')

    fh = tot = fh_x = tot_x = 0.0
    over50 = 0
    for r in q['data']:
        p = px.get(r[0])
        iss, held = num(r[i_iss]), num(r[i_fh])
        if not p or iss is None or held is None:
            continue
        fh += held * p
        tot += iss * p
        if r[0] != '2330':
            fh_x += held * p
            tot_x += iss * p
        if (num(r[i_pct]) or 0) >= 50:
            over50 += 1
    if tot <= 0:
        return None
    return {
        'date': date,
        'ratio': round(fh / tot * 100, 3),
        'exTsmc': round(fh_x / tot_x * 100, 3),
        'fhValue': round(fh / 1e8),
        'totalValue': round(tot / 1e8),
        'over50': over50,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=250)
    ap.add_argument('--since', help='YYYY-MM-DD，指定時忽略 --days')
    args = ap.parse_args()

    existing = []
    if os.path.exists(OUT):
        existing = json.load(open(OUT, encoding='utf-8'))
    have = {r['date'] for r in existing}

    taiex = json.load(open(TAIEX, encoding='utf-8'))
    dates = sorted({r['date'] for r in taiex})
    dates = [d for d in dates if d >= args.since] if args.since else dates[-args.days:]
    todo = sorted((d for d in dates if d not in have), reverse=True)
    print(f'foreign-holding: {len(have)} 筆已有，待補 {len(todo)} 日', flush=True)

    added = 0
    for n, d in enumerate(todo, 1):
        row = compute(d)
        if row is None:
            print(f'  {d}: 無資料（休市或尚未公布）', flush=True)
        else:
            existing.append(row)
            added += 1
            print(f'  {d}: {row["ratio"]}% ex台積電 {row["exTsmc"]}% ≥50%:{row["over50"]}檔', flush=True)
        if added and (n % 10 == 0 or n == len(todo)):
            save(existing)
    if added:
        save(existing)
    print(f'foreign-holding: 新增 {added} 筆，共 {len(existing)} 筆', flush=True)


def save(rows):
    rows.sort(key=lambda r: r['date'])
    with open(OUT, 'w', encoding='utf-8') as fp:
        json.dump(rows, fp, ensure_ascii=False, separators=(',', ':'))


if __name__ == '__main__':
    sys.exit(main())
