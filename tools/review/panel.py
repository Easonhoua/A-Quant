"""指标快算层。

为什么需要它：`backend.engine.features()` 每次都从第一根 K 线重算整条序列，而
`signals()` 又在每一天调用一次，于是"扫一年全市场"是 O(n^2)。这里把同一套
Wilder 递推**每只股票只算一遍**，之后按天取数。

对齐关系（与 `features(bars[:t+1])` 严格一致，`selfcheck()` 会验证）：
    atr[t]     = wilder(tr, 14)[t-14]
    rsi[t]     = wilder(changes, 14)[t-14]
    plus_di[t] = 100*ps[k]/trs[k]，其中 k = t-14
    adx[t]     = wilder(dx, 14)[t-27]

Wilder 平滑是因果的：截断序列不改变已算出的历史值，所以"一次算完整条"与
"每天重算前缀"在数值上等价（浮点求和顺序的差异另计）。

不依赖 numpy：单只股票只算一遍，纯列表足够快，且评审工具不该给项目新增依赖。
"""

from backend.engine import STRATEGY, RULES

# 与 features() 保持一致的指标周期
ATR_PERIOD = STRATEGY["atr_period"]
RSI_PERIOD = STRATEGY["rsi_period"]
ADX_PERIOD = STRATEGY["adx_period"]


def wilder(values, period):
    """Wilder 平滑：以前 period 个值的均值做种子，之后按 (prev*(p-1)+x)/p 递推。"""
    if len(values) < period:
        return []
    out = [sum(values[:period]) / period]
    for value in values[period:]:
        out.append((out[-1] * (period - 1) + value) / period)
    return out


def _prefix(values):
    """前缀和，长度 n+1；用于 O(1) 求区间[均值]。"""
    out = [0.0]
    total = 0.0
    for value in values:
        total += value
        out.append(total)
    return out


class Panel:
    """一只股票的全序列指标；`at(t)` 等价于 engine.features(bars[:t+1])。"""

    __slots__ = ("code", "name", "n", "dates", "o", "h", "l", "c", "v",
                 "atr", "rsi", "adx", "pdi", "mdi", "clean",
                 "ma5", "ma20", "ma20p", "ma60", "vr", "amt20", "ph20", "pl20", "atrd", "ret20")

    def __init__(self, stock):
        bars = stock["bars"]
        self.code = stock["code"]
        self.name = stock.get("name", "")
        self.dates = [b["date"] for b in bars]
        self.o = [b["open"] for b in bars]
        self.h = [b["high"] for b in bars]
        self.l = [b["low"] for b in bars]
        self.c = [b["close"] for b in bars]
        self.v = [b["volume"] for b in bars]
        amount = [b["amount"] for b in bars]
        n = self.n = len(bars)

        tr, plus, minus = [], [], []
        for i in range(1, n):
            prev_close = self.c[i - 1]
            tr.append(max(self.h[i] - self.l[i],
                          abs(self.h[i] - prev_close),
                          abs(self.l[i] - prev_close)))
            up = self.h[i] - self.h[i - 1]
            down = self.l[i - 1] - self.l[i]
            plus.append(up if up > down and up > 0 else 0.0)
            minus.append(down if down > up and down > 0 else 0.0)
        changes = [self.c[i] - self.c[i - 1] for i in range(1, n)]

        self.atr = self._place(wilder(tr, ATR_PERIOD), ATR_PERIOD)
        gains = wilder([x if x > 0 else 0.0 for x in changes], RSI_PERIOD)
        losses = wilder([-x if x < 0 else 0.0 for x in changes], RSI_PERIOD)
        rsi = []
        for gain, loss in zip(gains, losses):
            rsi.append(50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)))
        self.rsi = self._place(rsi, RSI_PERIOD)

        # AVG(TR) 与 AVG(+DM)/AVG(-DM) 用同一套 Wilder 平滑
        trs = wilder(tr, ADX_PERIOD)
        pdi_raw, mdi_raw = [], []
        for p, m, t in zip(wilder(plus, ADX_PERIOD), wilder(minus, ADX_PERIOD), trs):
            pdi_raw.append(100 * p / t if t else 0.0)
            mdi_raw.append(100 * m / t if t else 0.0)
        dx = [100 * abs(p - m) / (p + m) if p + m else 0.0 for p, m in zip(pdi_raw, mdi_raw)]
        self.pdi = self._place(pdi_raw, ADX_PERIOD)
        self.mdi = self._place(mdi_raw, ADX_PERIOD)
        self.adx = self._place(wilder(dx, ADX_PERIOD), 2*ADX_PERIOD-1)

        cs, vs, als = _prefix(self.c), _prefix(self.v), _prefix(amount)
        self.ma5 = self._mean_series(cs, 5)
        self.ma20 = self._mean_series(cs, 20)
        self.ma60 = self._mean_series(cs, 60)
        self.ma20p = [None] * 20 + [(cs[t] - cs[t - 20]) / 20 for t in range(20, n)]

        self.vr = [None] * 20
        self.amt20 = [None] * 19
        self.ret20 = [None] * 20
        self.atrd = [None] * n
        for t in range(19, n):
            self.amt20.append((als[t + 1] - als[t - 19]) / 20)
        for t in range(20, n):
            base = (vs[t] - vs[t - 20]) / 20
            self.vr.append(self.v[t] / base if base else 0.0)
            self.ret20.append(self.c[t] / self.c[t - 20] - 1)
        self.amt20 = self.amt20[:n]
        self.vr = self.vr[:n]
        self.ret20 = self.ret20[:n]

        # 唐奇安通道：不含当日
        self.ph20, self.pl20 = [None] * 20, [None] * 20
        for t in range(20, n):
            window_h = self.h[t - 20:t]
            window_l = self.l[t - 20:t]
            self.ph20.append(max(window_h))
            self.pl20.append(min(window_l))

        for t in range(59, n):
            atr = self.atr[t]
            if atr:
                self.atrd[t] = (self.c[t] - self.ma20[t]) / atr

        # 与 engine.price_anomalies 一致：近 61 根里出现超过涨跌停幅度的跳空，
        # 该日视为"价格不可信"，不能进池（不复权下的除权/送转）。
        self.clean = [False] * n
        limit = 0.20 if self.code.startswith(("300", "301")) else 0.10
        last_bad = -10 ** 9
        for t in range(n):
            prev = self.c[t - 1] if t else 0.0
            if t and prev > 0 and abs(self.c[t] / prev - 1) > limit + 0.011:
                last_bad = t
            self.clean[t] = last_bad <= t - 60

    @staticmethod
    def _place(values, offset):
        """把长度 n-offset 的序列对齐到日索引。"""
        return [None] * offset + list(values)

    @staticmethod
    def _mean_series(cs, window):
        n = len(cs) - 1
        return [None] * (window - 1) + [(cs[t + 1] - cs[t - window + 1]) / window
                                        for t in range(window - 1, n)]

    def ready(self, t):
        return 59 <= t < self.n and bool(self.atr[t]) and self.atr[t] > 0 and self.adx[t] is not None

    def at(self, t):
        """等价于 engine.features(bars[:t+1])；历史不足 60 根时返回 None。"""
        if not self.ready(t):
            return None
        return {
            "close": self.c[t], "ma5": self.ma5[t], "ma20": self.ma20[t],
            "ma60": self.ma60[t], "ma20_previous": self.ma20p[t],
            "previous_close": self.c[t - 1], "atr": self.atr[t], "rsi": self.rsi[t],
            "adx": self.adx[t], "plus_di": self.pdi[t], "minus_di": self.mdi[t],
            "atr_distance": self.atrd[t], "amount20": self.amt20[t],
            "volume_ratio": self.vr[t], "return20": self.ret20[t],
            "prior_high20": self.ph20[t], "prior_low20": self.pl20[t],
        }


def flags(f, strict=False, adx_filter=False):
    """与 engine.signals 完全相同的判定，返回 (is_s1, is_s2)。"""
    from backend.engine import signals_from_features
    selected={m for m,_ in signals_from_features(f,adx_filter,strict)}
    return 'S1' in selected, 'S2' in selected


def pool_ok(panel, t):
    """股票池的逐日条件：有成交 + 20 日均额达标 + 近期无异常跳空。

    板块/ST/历史长度由调用方先筛；"无异常跳空"对应 engine 的 price_anomalies，
    因为不复权价格下的除权跳空会让 MA/ATR/通道全部失真。
    """
    if t < 59 or panel.v[t] <= 0 or not panel.clean[t]:
        return False
    amount20 = panel.amt20[t]
    return amount20 is not None and amount20 >= RULES["min_amount20"]


def iter_panels(snapshot):
    """遍历股票池内的股票，产出 Panel（板块/ST/数据合法性已过滤）。"""
    from backend.engine import allowed, valid_bars
    for stock in snapshot["stocks"]:
        if not allowed(stock["code"], stock["name"]):
            continue
        bars = stock.get("bars") or []
        if len(bars) < 61 or not valid_bars(bars):
            continue
        yield Panel(stock)


def selfcheck(snapshot, trials=300, seed=11):
    """随机抽 (股票, 日)，逐字段对比 Panel.at 与 engine.features。"""
    import random
    from backend.engine import features

    rng = random.Random(seed)
    stocks = [s for s in snapshot["stocks"] if len(s.get("bars") or []) >= 61]
    if not stocks:
        return {"pairs": 0, "max_rel_diff": None}
    worst, checked = 0.0, 0
    for _ in range(trials):
        stock = rng.choice(stocks)
        bars = stock["bars"]
        if len(bars) < 61:
            continue
        t = rng.randrange(59, len(bars))
        reference = features(bars[:t + 1])
        fast = Panel(stock).at(t)
        if fast is None:
            continue
        for key, ref_value in reference.items():
            fast_value = fast.get(key)
            if ref_value is None or fast_value is None:
                continue
            worst = max(worst, abs(ref_value - fast_value) / max(1.0, abs(ref_value)))
        checked += 1
    return {"pairs": checked, "max_rel_diff": worst}


def signal_agreement(snapshot, as_of=None, limit=None):
    """逐股对照 engine.signals：返回一致/不一致数量与不一致样本。"""
    from backend.engine import allowed, valid_bars, signals

    as_of = as_of or snapshot["as_of"]
    agree = disagree = 0
    examples = []
    for stock in snapshot["stocks"]:
        code, name = stock["code"], stock["name"]
        bars = stock.get("bars") or []
        if not allowed(code, name) or not valid_bars(bars) or len(bars) < 61:
            continue
        if bars[-1]["date"] != as_of:
            continue
        panel = Panel(stock)
        t = panel.n - 1
        f = panel.at(t)
        if f is None:
            continue
        fast = {m for m, on in zip(("S1", "S2"), flags(f)) if on}
        reference = {m for m, _ in signals(bars)}
        if fast == reference:
            agree += 1
        else:
            disagree += 1
            if len(examples) < 5:
                examples.append({"code": code, "engine": sorted(reference), "fast": sorted(fast)})
        if limit and agree + disagree >= limit:
            break
    return {"agree": agree, "disagree": disagree, "examples": examples}
