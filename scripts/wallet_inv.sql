WITH f AS MATERIALIZED (
  SELECT p.ts, p.px, p.notional, 1 AS dir, (p.side = 'B') AS taker
  FROM tape_prints p INDEXED BY idx_tape_buyer
  WHERE p.buyer = :id AND p.coin = 'BTC'
  UNION ALL
  SELECT p.ts, p.px, p.notional, -1, (p.side <> 'B')
  FROM tape_prints p INDEXED BY idx_tape_seller
  WHERE p.seller = :id AND p.coin = 'BTC'
),
b AS MATERIALIZED (
  SELECT (ts / 300000) * 300000 AS bucket,
         sum(CASE WHEN taker THEN 0 ELSE dir * notional END) AS mf,
         sum(CASE WHEN taker THEN dir * notional ELSE 0 END) AS tf,
         sum(px * notional) / sum(notional) AS vwap
  FROM f GROUP BY bucket
),
d AS (
  SELECT mf, tf, vwap,
         SUM(mf + tf) OVER (ORDER BY bucket) AS inv,
         SUM(mf + tf) OVER (ORDER BY bucket
           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS inv_prev,
         vwap - LAG(vwap) OVER (ORDER BY bucket) AS dpx
  FROM b
),
mu AS (
  SELECT avg(mf) AS amf, avg(tf) AS atf, avg(dpx) AS ap,
         avg(inv) AS ai, avg(vwap) AS av, avg(inv_prev) AS aip
  FROM d WHERE dpx IS NOT NULL
),
c AS (
  SELECT count(*) AS n,
         sum((dpx - ap) * (dpx - ap)) AS vp,
         sum((mf - amf) * (dpx - ap)) AS cm, sum((mf - amf) * (mf - amf)) AS vm,
         sum((tf - atf) * (dpx - ap)) AS ct, sum((tf - atf) * (tf - atf)) AS vt,
         sum((inv - ai) * (vwap - av)) AS ci,
         sum((vwap - av) * (vwap - av)) AS vv,
         sum((inv - ai) * (inv - ai)) AS vi,
         sum((inv_prev - aip) * (tf - atf)) AS cht,
         sum((inv_prev - aip) * (mf - amf)) AS chm,
         sum((inv_prev - aip) * (inv_prev - aip)) AS vip,
         min(inv) AS lo, max(inv) AS hi
  FROM d, mu WHERE dpx IS NOT NULL
)
SELECT n,
       round(cm / vp / 1e3, 1) AS maker_k, round(cm / sqrt(vp * vm), 3) AS maker_corr,
       round(ct / vp / 1e3, 1) AS taker_k, round(ct / sqrt(vp * vt), 3) AS taker_corr,
       round(ci / sqrt(vv * vi), 3) AS inv_corr,
       round(cht / vip, 5) AS hl_taker,
       round(chm / vip, 5) AS hl_maker,
       round(lo / 1e6, 1) AS inv_min_m,
       round(hi / 1e6, 1) AS inv_max_m
FROM c;
