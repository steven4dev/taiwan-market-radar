#!/usr/bin/env python3
"""M12 回測標的：還原收盤價（adj close）→ data/assets/<代碼>.json。

來源 Yahoo chart API（含股息、分割還原），以 curl 取得（避免 Python 憑證問題）。
  python3 scripts/update_assets.py              # 每日更新：抓近 1 年，與既有資料合併
  python3 scripts/update_assets.py --full       # 初次建立：抓全部歷史
  python3 scripts/update_assets.py 0050 2330    # 只更新指定代碼

還原價會因新除息而「整段往前改寫」。合併時以重疊期間的比值，把舊資料整段等比例校正，
避免新舊資料在接點出現跳空。
"""
import datetime, json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'data', 'assets')
KEEP_FROM = '2008-01-01'   # M12 最長看 15 年（持股資料自 2011-10 起），更早的資料用不到

# 代碼 → 顯示名稱（加權指數沿用 taiex 資料，不在此列）
ASSETS = {
    '0050': '0050 元大台灣50',
    '0056': '0056 元大高股息',
    '006208': '006208 富邦台50',
    '2330': '2330 台積電',
    '2317': '2317 鴻海',
    '2454': '2454 聯發科',
    '00631L': '00631L 台灣50正2',
    '00685L': '00685L 加權正2',
}


def fetch(code, rng):
    # range=max 會被 Yahoo 自動降為月資料，全史改用 period1/period2 強制日資料
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    span = f'period1=0&period2={now}' if rng == 'max' else f'range={rng}'
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW?interval=1d&{span}&events=div%2Csplit'
    p = subprocess.run(['curl', '-s', '--max-time', '40', '-A', 'Mozilla/5.0', url],
                       capture_output=True)
    res = json.loads(p.stdout.decode('utf-8'))['chart']['result'][0]
    adj = res['indicators']['adjclose'][0]['adjclose']
    out = {}
    for ts, v in zip(res['timestamp'], adj):
        if v is None:
            continue
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
        out[d] = float(v)
    return out


def merge(old, new):
    """old/new: {date: adj}。回傳 (合併結果, 校正比值)。"""
    overlap = sorted(set(old) & set(new))
    factor = 1.0
    if overlap:
        d0 = overlap[0]
        factor = new[d0] / old[d0]
        if abs(factor - 1) > 1e-6:
            old = {d: v * factor for d, v in old.items() if d < d0}
        else:
            old = {d: v for d, v in old.items() if d < d0}
    merged = dict(old)
    merged.update(new)
    return merged, factor


def clean(series):
    """修正未還原的分割／面額變更：台股單日漲跌幅上限為 7～10%，任何單日變動超過 -20%／+25%
    必為資料瑕疵（例如 0050 在 2014-01 單日 -75%）。以接點比值把更早的資料整段往後接齊，
    使價格連續；代價是該日 ≤10% 的真實漲跌被抹平。回傳 (修正後, 修正清單)。"""
    dates = sorted(series)
    vals = {d: series[d] for d in dates}
    fixes = []
    for i in range(len(dates) - 1, 0, -1):          # 由新到舊，逐段把舊資料縮放到與新資料連續
        d0, d1 = dates[i - 1], dates[i]
        r = vals[d1] / vals[d0]
        if r < 0.8 or r > 1.25:
            for k in range(i):
                vals[dates[k]] *= r
            fixes.append((d0, d1, r))
    return vals, fixes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    full = '--full' in sys.argv
    codes = args or list(ASSETS)
    os.makedirs(OUT_DIR, exist_ok=True)
    for code in codes:
        path = os.path.join(OUT_DIR, f'{code}.json')
        try:
            old = {}
            if os.path.exists(path) and not full:
                old = {d: v for d, v in json.load(open(path, encoding='utf-8'))['rows']}
            new = fetch(code, 'max' if (full or not old) else '1y')
            if len(new) < 20:
                raise RuntimeError(f'僅取得 {len(new)} 筆')
            merged, factor = merge(old, new)
            merged, fixes = clean(merged)
            rows = [[d, round(merged[d], 4)] for d in sorted(merged) if d >= KEEP_FROM]
            with open(path, 'w', encoding='utf-8') as fp:
                json.dump({'code': code, 'label': ASSETS.get(code, code), 'rows': rows},
                          fp, ensure_ascii=False, separators=(',', ':'))
            note = f'，舊資料校正 ×{factor:.4f}' if old and abs(factor - 1) > 1e-6 else ''
            for d0, d1, r in fixes:
                print(f'  修正 {d0}→{d1} 單日比值 {r:.3f}')
            print(f'assets {code}: {len(rows)} 筆 {rows[0][0]}~{rows[-1][0]} 最新 {rows[-1][1]}{note}')
        except Exception as e:
            print(f'assets {code}: 失敗 {e}')


if __name__ == '__main__':
    main()
