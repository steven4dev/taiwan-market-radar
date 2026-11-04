#!/usr/bin/env python3
"""M7 外部環境：費城半導體指數 (^SOX) 日收盤 → data/sox.json。

伺服器端直接呼叫 Yahoo chart API（無 CORS 限制），取代前端依賴公用 CORS 代理。
同日期以新抓到的為準（當日若為未收盤的暫時值，下一次更新會覆蓋）。
"""
import datetime, json, os, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'sox.json')
URL = 'https://query1.finance.yahoo.com/v8/finance/chart/%5ESOX?interval=1d&range=6mo'
KEEP = 300


def main():
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)['chart']['result'][0]

    rows = {}
    if os.path.exists(OUT):
        rows = {r['date']: r['close'] for r in json.load(open(OUT, encoding='utf-8'))}

    fetched = 0
    for ts, close in zip(res['timestamp'], res['indicators']['quote'][0]['close']):
        if close is None:
            continue
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
        rows[d] = round(close, 2)
        fetched += 1

    out = [{'date': d, 'close': rows[d]} for d in sorted(rows)][-KEEP:]
    with open(OUT, 'w', encoding='utf-8') as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(',', ':'))
    print(f'sox: 抓到 {fetched} 筆，共 {len(out)} 筆，最新 {out[-1]["date"]} = {out[-1]["close"]}')


if __name__ == '__main__':
    main()
