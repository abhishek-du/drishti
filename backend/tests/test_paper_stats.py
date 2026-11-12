from drishti.paper.stats import equity_stats, trade_stats


def test_trade_stats_net_of_costs():
    t = [dict(net_pnl=2000, r_multiple=2.0, costs=50, holding_sessions=3, reason_out="target 110 reached"),
         dict(net_pnl=-1000, r_multiple=-1.0, costs=40, holding_sessions=2, reason_out="stop 95 hit"),
         dict(net_pnl=-200, r_multiple=-0.2, costs=30, holding_sessions=5, reason_out="time stop: x")]
    s = trade_stats(t)
    assert s["trades"] == 3 and s["net_pnl"] == 800 and s["hit_rate"] == 0.3333
    assert s["payoff_ratio"] == round(2000 / 600, 4) and s["expectancy_rs"] == 266.67
    assert s["expectancy_r"] == 0.2667 and s["costs"] == 120
    assert s["exits"] == {"target": 1, "stop": 1, "time": 1}
    assert trade_stats([]) == {"trades": 0}


def test_equity_stats_drawdown_and_benchmark():
    rows = [dict(equity=1_010_000, benchmark_equity=1_005_000),
            dict(equity=959_500, benchmark_equity=1_000_000),
            dict(equity=1_020_000, benchmark_equity=1_030_000)]
    e = equity_stats(rows, 1_000_000)
    assert e["total_return"] == 0.02 and e["max_drawdown"] == -0.05
    assert e["benchmark_return"] == 0.03 and e["excess_return"] == -0.01
