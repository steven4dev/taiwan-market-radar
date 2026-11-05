#!/usr/bin/env python3
"""外資持股占上市總市值（M10）：回補 / 每日增量更新 data/foreign-holding.json。

來源：TWSE MI_QFIIS（外資及陸資持股統計）+ MI_INDEX（每日收盤行情）。
用 curl 抓取（TWSE 憑證缺 Subject Key Identifier，Python 3.13+ 的嚴格 SSL 會擋）。
日期清單取自 data/taiex.json 的交易日。

用法：
  python3 scripts/foreign_holding.py            # 補齊缺的交易日（最近 250 日內）
  python3 scripts/foreign_holding.py --days 60  # 只看最近 60 個交易日
  python3 scripts/foreign_holding.py --since 2021-11-01  # 回補到指定日期（由新到舊）
  python3 scripts/foreign_holding.py --weekly-since 2011-10-01 --sleep 2.5
      # 更早的歷史改為每週取一筆（週五優先，休市則順延到週四…週一），供 10/15 年長期圖使用
"""
import argparse, datetime, json, os, subprocess, sys, time

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


def find_field(fields, *keys):
    for i, f in enumerate(fields):
        if all(k in f for k in keys):
            return i
    raise ValueError(f'欄位不存在：{keys}')


def compute(date):
    ymd = date.replace('-', '')
    q = fetch(f'{BASE}/fund/MI_QFIIS?date={ymd}&selectType=ALLBUT0999&response=json')
    time.sleep(SLEEP)
    if not q or q.get('stat') != 'OK' or not q.get('data'):
        return None
    m = fetch(f'{BASE}/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json')
    time.sleep(SLEEP)
    if not m or m.get('stat') != 'OK':
        return None

    tbl = next((t for t in m.get('tables', []) if '每日收盤行情' in (t.get('title') or '')), None)
    if not tbl or not tbl.get('data'):
        return None
    f = tbl['fields']
    ic, ip = f.index('證券代號'), f.index('收盤價')
    px = {r[ic]: num(r[ip]) for r in tbl['data']}

    # 2015 年前欄位名無「及陸資」，以關鍵字容錯比對
    qf = q['fields']
    i_iss = find_field(qf, '發行股數')
    i_fh = find_field(qf, '全體外資', '持有股數')
    i_pct = find_field(qf, '全體外資', '持股比率')

    idx = None
    for t in m.get('tables', []):
        flds = t.get('fields') or []
        if '收盤指數' in flds:
            ci = flds.index('收盤指數')
            for r in t.get('data') or []:
                if r[0] == '發行量加權股價指數':
                    idx = num(r[ci])
            if idx is not None:
                break

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
    row = {
        'date': date,
        'ratio': round(fh / tot * 100, 3),
        'exTsmc': round(fh_x / tot_x * 100, 3),
        'fhValue': round(fh / 1e8),
        'totalValue': round(tot / 1e8),
        'over50': over50,
    }
    if idx is not None:
        row['idx'] = round(idx, 2)
    return row


def weekly_candidates(since, before):
    """由新到舊，每週一組候選日（週五→週一），涵蓋 since 之後、before 之前。"""
    end = datetime.date.fromisoformat(before) - datetime.timedelta(days=1)
    end -= datetime.timedelta(days=(end.weekday() - 4) % 7)  # 退到最近的週五
    start = datetime.date.fromisoformat(since)
    weeks = []
    while end >= start:
        weeks.append([(end - datetime.timedelta(days=k)).isoformat() for k in range(5)])
        end -= datetime.timedelta(days=7)
    return weeks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=250)
    ap.add_argument('--since', help='YYYY-MM-DD，指定時忽略 --days')
    ap.add_argument('--weekly-since', help='YYYY-MM-DD，更早的歷史改為每週取一筆（由新到舊）')
    ap.add_argument('--before', help='YYYY-MM-DD，每週回補的上界（預設為檔內最早日期）')
    ap.add_argument('--sleep', type=float, help='請求間隔秒數（預設 4；TWSE 約 3 次/5 秒封鎖）')
    args = ap.parse_args()
    global SLEEP
    if args.sleep:
        SLEEP = args.sleep

    existing = []
    if os.path.exists(OUT):
        existing = json.load(open(OUT, encoding='utf-8'))
    have = {r['date'] for r in existing}

    if args.weekly_since:
        before = args.before or min(have)
        weeks = weekly_candidates(args.weekly_since, before)
        print(f'foreign-holding: 每週回補 {args.weekly_since} ~ {before}，共 {len(weeks)} 週', flush=True)
        added = 0
        for week in weeks:
            if any(d in have for d in week):
                continue
            for d in week:
                row = compute(d)
                if row is not None:
                    existing.append(row)
                    have.add(d)
                    added += 1
                    print(f'  {d}: {row["ratio"]}% 指數 {row.get("idx")}', flush=True)
                    break
            else:
                print(f'  {week[0]} 所在週：整週無資料', flush=True)
            if added and added % 10 == 0:
                save(existing)
        if added:
            save(existing)
        print(f'foreign-holding: 新增 {added} 筆，共 {len(existing)} 筆', flush=True)
        return

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
