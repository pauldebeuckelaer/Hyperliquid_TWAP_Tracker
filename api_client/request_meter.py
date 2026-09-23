#!/usr/bin/env python3
"""
Request Meter
=============
Per-minute ledger of every Hyperliquid /info request THIS process sends.

One line per UTC minute via logger 'api.load':
  2026-09-20 18:10 | weight 1134/1200 | req 412 | 429 3 | err 0 ⚠ | top sources...

Purpose: when the Hyperliquid UI shows "Rate Limited", look up that minute.
The box and the browser share one public IP, so they share one budget.
UI clock is Belgian local time — subtract 2h (CEST) / 1h (CET) for UTC.

Every ATTEMPT is recorded (retries cost weight too). Unknown request types
default to weight 20. Covers this process only — orderbook tracker and
transfer poller run as separate services and are NOT in these numbers.

Thread-safe: the WS discovery thread shares this singleton.
"""
import logging
import threading
import time
from collections import Counter

logger = logging.getLogger('api.load')

IP_BUDGET_PER_MIN = 2000  # warn threshold only (flag at 80%). Documented limit is 1200; measured rejections start ~2500 (Sep 21 meter).
DEFAULT_WEIGHT = 20
WEIGHTS = {
    'clearinghouseState': 2,
    'spotClearinghouseState': 2,
    'l2Book': 2,
    'allMids': 2,
    'orderStatus': 2,
    'exchangeStatus': 2,
    'userRole': 60,
}


class RequestMeter:
    def __init__(self):
        self._lock = threading.Lock()
        self._minute = None
        self._reset()

    def _reset(self):
        self._weight = Counter()
        self._requests = 0
        self._rate_limited = 0
        self._errors = 0

    def record(self, request_type: str, source: str, status) -> None:
        """status: HTTP status int, or 'timeout' / 'err' for failed attempts."""
        minute = time.strftime('%Y-%m-%d %H:%M', time.gmtime())
        w = WEIGHTS.get(request_type, DEFAULT_WEIGHT)
        with self._lock:
            if minute != self._minute:
                self._flush_locked()
                self._minute = minute
            self._weight[f"{source}/{request_type}"] += w
            self._requests += 1
            if status == 429:
                self._rate_limited += 1
            elif status != 200:
                self._errors += 1

    def _flush_locked(self) -> None:
        if self._minute is not None and self._requests:
            total = sum(self._weight.values())
            hot = total >= IP_BUDGET_PER_MIN * 0.8 or self._rate_limited
            top = ' · '.join(f"{k} {v}" for k, v in self._weight.most_common(6))
            logger.info(
                f"{self._minute} | weight {total}/{IP_BUDGET_PER_MIN} | "
                f"req {self._requests} | 429 {self._rate_limited} | "
                f"err {self._errors}{' ⚠' if hot else ''} | {top}"
            )
        self._reset()


METER = RequestMeter()