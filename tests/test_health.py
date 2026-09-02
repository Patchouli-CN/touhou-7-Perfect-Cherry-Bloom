"""engine.health.HealthCenter —— 帧预算超支累计/阈值告警/节流 单测。"""

from touhou.engine.health import HealthCenter

_BUDGET = 1000.0 / 60  # 单帧预算(60fps)


def _make() -> tuple[HealthCenter, list[float]]:
    """注入假时钟的 HealthCenter; 返回 (实例, 可变当前时刻)。"""
    now = [1000.0]
    return HealthCenter("renderer", now=lambda: now[0]), now


def test_under_budget_no_warn() -> None:
    hc, _ = _make()
    for _ in range(100):
        assert not hc.tick(_BUDGET)  # 恰好花满预算: 不累计不告警
    assert hc.behind_ms == 0.0


def test_overrun_accumulates_and_warns_at_threshold() -> None:
    hc, _ = _make()
    # 每帧落后 10ms; 阈值 40 帧预算 ≈ 666.7ms → 第 67 帧越线告警
    for i in range(66):
        assert not hc.tick(_BUDGET + 10.0), f"第 {i + 1} 帧不应告警"
    assert hc.tick(_BUDGET + 10.0)
    assert hc.behind_ms == 0.0  # 告警后清零重新累计


def test_fast_frames_pay_back_debt() -> None:
    hc, _ = _make()
    hc.tick(_BUDGET + 20.0)
    hc.tick(_BUDGET + 20.0)
    assert hc.behind_ms == 40.0
    hc.tick(_BUDGET - 30.0)  # 快帧偿还落后量
    assert hc.behind_ms == 10.0
    hc.tick(_BUDGET - 30.0)  # 偿还到 0 为止, 不为负
    assert hc.behind_ms == 0.0


def test_throttle_suppresses_repeat_warn() -> None:
    hc, now = _make()
    for _ in range(66):
        hc.tick(_BUDGET + 10.0)
    assert hc.tick(_BUDGET + 10.0)  # 首次告警, 清零
    for _ in range(200):  # 节流窗口内累计远超阈值: 不告警也不清零
        assert not hc.tick(_BUDGET + 10.0)
    assert hc.behind_ms > hc.warn_ms
    now[0] += 6.0  # 越过节流窗口
    assert hc.tick(_BUDGET + 10.0)  # 再次告警并清零
    assert hc.behind_ms == 0.0


def test_subject_carried() -> None:
    hc, _ = _make()
    assert hc.subject == "renderer"
