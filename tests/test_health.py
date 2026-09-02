"""engine.health.HealthCenter —— 滑动窗口实测帧率告警 单测。"""

from touhou.engine.health import HealthCenter

_BUDGET = 1000.0 / 60  # 单帧预算(60fps)
_WINDOW = 60  # 默认窗口 1s = 60 帧


def _make(**kw) -> tuple[HealthCenter, list[float]]:
    """注入假时钟的 HealthCenter; 返回 (实例, 可变当前时刻)。"""
    now = [1000.0]
    return HealthCenter("renderer", now=lambda: now[0], **kw), now


def test_full_budget_frames_no_warn() -> None:
    hc, _ = _make()
    for _ in range(300):
        assert not hc.tick(_BUDGET)  # 满帧 60fps: 不告警


def test_slight_overrun_no_warn() -> None:
    """持续 17ms/帧(≈58.8fps, 肉眼无感)永不告警 —— 改实测帧率判据的核心动机。"""
    hc, _ = _make()
    for _ in range(3000):
        assert not hc.tick(17.0)


def test_sustained_low_fps_warns() -> None:
    hc, _ = _make()
    for i in range(_WINDOW - 1):
        assert not hc.tick(_BUDGET * 2), f"窗口未满(第 {i + 1} 帧)不应告警"
    assert hc.tick(_BUDGET * 2)  # 窗口填满, 平均 30fps < 50: 告警


def test_transient_warmup_no_warn() -> None:
    """启动初期窗口未满时不告警(开头几帧慢是常态)。"""
    hc, _ = _make()
    for _ in range(_WINDOW - 1):
        assert not hc.tick(500.0)  # 每帧 500ms 也不告, 窗口没满


def test_single_spike_visible_hitch_warns() -> None:
    """满帧中夹一次 500ms 大卡顿: 窗口平均跌破阈值, 一次告警。"""
    hc, _ = _make()
    for _ in range(_WINDOW - 1):
        hc.tick(_BUDGET)
    assert hc.tick(_BUDGET + 500.0)  # 平均 ≈40fps < 50


def test_recovery_no_warn() -> None:
    hc, now = _make()
    for _ in range(_WINDOW):
        hc.tick(_BUDGET * 2)  # 30fps 时段, 触发告警
    for _ in range(_WINDOW):  # 恢复满帧: 窗口内慢帧逐步换出(节流窗口内不告警)
        assert not hc.tick(_BUDGET)
    now[0] += 6.0  # 节流窗口已过, 且帧率已恢复满帧
    for _ in range(_WINDOW):
        assert not hc.tick(_BUDGET)


def test_throttle_suppresses_repeat_warn() -> None:
    hc, now = _make()
    for _ in range(_WINDOW):
        hc.tick(_BUDGET * 2)
    # 已告警; 持续低帧率, 节流窗口内不再告警
    for _ in range(200):
        assert not hc.tick(_BUDGET * 2)
    now[0] += 6.0  # 越过节流窗口
    assert hc.tick(_BUDGET * 2)  # 仍在卡顿: 再次告警


def test_subject_carried() -> None:
    hc, _ = _make()
    assert hc.subject == "renderer"
