#!/usr/bin/env python3
"""M12 回測標的：還原 OHLC（K 線）→ data/assets/<代碼>.json。

來源 Yahoo chart API（含股息、分割還原），以 curl 取得（避免 Python 憑證問題）。
檔案格式：{"code","label","rows":[[date, open, high, low, close], ...]}，皆為還原價。
  python3 scripts/update_assets.py              # 每日更新：抓近 1 年，與既有資料合併
  python3 scripts/update_assets.py --full       # 初次建立：抓全部歷史
  python3 scripts/update_assets.py 0050 2330    # 只更新指定代碼

還原價會因新除息而「整段往前改寫」。合併時以重疊期間的收盤比值，把舊資料整段等比例校正，
避免新舊資料在接點出現跳空。
"""
import datetime, json, os, subprocess, sys
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'data', 'assets')
KEEP_FROM = '2008-01-01'   # M12 最長看 15 年（持股資料自 2011-10 起），更早的資料用不到

# 代碼 → (顯示名稱, Yahoo 代號)
ASSETS = {
    'TAIEX': ('加權指數', '^TWII'),
    '0050': ('0050 元大台灣50', '0050.TW'),
    '006208': ('006208 富邦台50', '006208.TW'),
    '0052': ('0052 富邦科技', '0052.TW'),
    '0056': ('0056 元大高股息', '0056.TW'),
    '2330': ('2330 台積電', '2330.TW'),
    '2308': ('2308 台達電', '2308.TW'),
    '2317': ('2317 鴻海', '2317.TW'),
    '2454': ('2454 聯發科', '2454.TW'),
    '8299': ('8299 群聯', '8299.TWO'),   # 上櫃股，Yahoo 代號為 .TWO
    '00631L': ('00631L 台灣50正2', '00631L.TW'),
    '00685L': ('00685L 加權正2', '00685L.TW'),
}


def fetch(symbol, rng):
    # range=max 會被 Yahoo 自動降為月資料，全史改用 period1/period2 強制日資料
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    span = f'period1=0&period2={now}' if rng == 'max' else f'range={rng}'
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?interval=1d&{span}&events=div%2Csplit'
    p = subprocess.run(['curl', '-s', '--max-time', '40', '-A', 'Mozilla/5.0', url], capture_output=True)
    res = json.loads(p.stdout.decode('utf-8'))['chart']['result'][0]
    q = res['indicators']['quote'][0]
    adj = (res['indicators'].get('adjclose') or [{}])[0].get('adjclose')
    out = {}
    for i, ts in enumerate(res['timestamp']):
        o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        if None in (o, h, l, c) or c <= 0:
            continue
        f = (adj[i] / c) if adj and adj[i] else 1.0          # 還原係數（股息、分割）
        o, h, l, c = o * f, h * f, l * f, c * f
        hi, lo = max(o, h, l, c), min(o, h, l, c)             # 修正異常的高低點
        d = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime('%Y-%m-%d')
        out[d] = [o, hi, lo, c]
    return out


def scale(vals, f):
    return [v * f for v in vals]


def merge(old, new):
    """old/new: {date: [o,h,l,c]}。回傳 (合併結果, 校正比值)。"""
    overlap = sorted(set(old) & set(new))
    factor = 1.0
    if overlap:
        d0 = overlap[0]
        factor = new[d0][3] / old[d0][3]
        old = {d: scale(v, factor) for d, v in old.items() if d < d0}
    merged = dict(old)
    merged.update(new)
    return merged, factor


# 槓桿型 ETF 單日最多約 ±20%（指數漲跌停 10% × 2），門檻需放寬，避免把真實的重挫當成瑕疵抹平
LEVERAGED = {'00631L', '00685L'}


def clean(series, lo_hi=(0.8, 1.25)):
    """修正未還原的分割／面額變更：台股單日漲跌幅上限為 7～10%，任何單日收盤變動超過 -20%／+25%
    必為資料瑕疵（例如 0050 在 2014-01 單日 -75%）。以接點比值把更早的資料整段往後接齊，
    使價格連續；代價是該日 ≤10% 的真實漲跌被抹平。回傳 (修正後, 修正清單)。"""
    dates = sorted(series)
    vals = {d: list(series[d]) for d in dates}
    fixes = []
    for i in range(len(dates) - 1, 0, -1):          # 由新到舊，逐段把舊資料縮放到與新資料連續
        d0, d1 = dates[i - 1], dates[i]
        r = vals[d1][3] / vals[d0][3]
        if r < lo_hi[0] or r > lo_hi[1]:
            for k in range(i):
                vals[dates[k]] = scale(vals[dates[k]], r)
            fixes.append((d0, d1, r))
    return vals, fixes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    full = '--full' in sys.argv
    codes = args or list(ASSETS)
    os.makedirs(OUT_DIR, exist_ok=True)
    for code in codes:
        label, symbol = ASSETS[code]
        path = os.path.join(OUT_DIR, f'{code}.json')
        try:
            old = {}
            if os.path.exists(path) and not full:
                rows = json.load(open(path, encoding='utf-8'))['rows']
                if rows and len(rows[0]) == 5:          # 舊格式（僅收盤）則改為全量重建
                    old = {r[0]: r[1:] for r in rows}
            new = fetch(symbol, 'max' if (full or not old) else '1y')
            if len(new) < 20:
                raise RuntimeError(f'僅取得 {len(new)} 筆')
            merged, factor = merge(old, new)
            merged, fixes = clean(merged, (0.7, 1.4) if code in LEVERAGED else (0.8, 1.25))
            rows = [[d] + [round(v, 4) for v in merged[d]] for d in sorted(merged) if d >= KEEP_FROM]
            with open(path, 'w', encoding='utf-8') as fp:
                json.dump({'code': code, 'label': label, 'rows': rows}, fp, ensure_ascii=False, separators=(',', ':'))
            note = f'，舊資料校正 ×{factor:.4f}' if old and abs(factor - 1) > 1e-6 else ''
            for d0, d1, r in fixes:
                print(f'  修正 {d0}→{d1} 單日比值 {r:.3f}')
            print(f'assets {code}: {len(rows)} 筆 {rows[0][0]}~{rows[-1][0]} 最新收 {rows[-1][4]}{note}')
        except Exception as e:
            print(f'assets {code}: 失敗 {e}')


if __name__ == '__main__':
    main()
