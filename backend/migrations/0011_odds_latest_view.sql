-- The newest odds row per (alert, basis), flattened for the odds panel (user 2026-11-04).
-- thin_sample: the historical replay has fewer than 30 occurrences.
CREATE VIEW odds_latest AS
SELECT DISTINCT ON (o.alert_id, o.basis)
  o.id, o.alert_id, o.basis, o.symbol, a.session_date, a.tf, o.as_of, o.entry, o.stop,
  o.target, o.horizon, o.qty,
  (o.mc ->> 'target_first')::numeric   AS mc_target_first,
  (o.mc ->> 'stop_first')::numeric     AS mc_stop_first,
  (o.mc ->> 'neither')::numeric        AS mc_neither,
  (o.mc ->> 'green')::numeric          AS mc_green,
  (o.mc ->> 'avg_r_net')::numeric      AS mc_avg_r_net,
  (o.mc ->> 'median_r_net')::numeric   AS mc_median_r_net,
  (o.mc ->> 'p05_r_net')::numeric      AS mc_p05_r_net,
  (o.mc ->> 'p95_r_net')::numeric      AS mc_p95_r_net,
  (o.replay ->> 'target_first')::numeric AS replay_target_first,
  (o.replay ->> 'avg_r_net')::numeric  AS replay_avg_r_net,
  coalesce((o.replay ->> 'n')::int, 0) AS replay_n,
  coalesce((o.replay ->> 'n')::int, 0) < 30 AS thin_sample,
  o.p_target_mc, o.breakeven, o.verdict, o.seed, o.version,
  o.params ->> 'cost_model' AS cost_model, (o.params ->> 'slip_r')::numeric AS slip_r,
  o.data_as_of, o.label, o.caveat, o.created_at
FROM odds_result o JOIN scan_alert a ON a.id = o.alert_id
ORDER BY o.alert_id, o.basis, o.created_at DESC, o.id DESC;
GRANT SELECT ON odds_latest TO drishti_app;
