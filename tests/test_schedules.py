from datetime import UTC, datetime, timedelta

from factoryguard.domain.schedules import ArmingSchedule, TimeWindow
from factoryguard.storage.sqlite import SQLiteStore
from factoryguard.ui.schedule_dialog import format_windows, parse_windows


def test_schedule_handles_cross_midnight_holidays_and_override():
    weekly = ((TimeWindow(22 * 60, 2 * 60),), (), (), (), (), (), ())
    schedule = ArmingSchedule(
        "stream-1",
        weekly=weekly,
        holidays=frozenset({"2026-10-20"}),
        default_armed=False,
        timezone="UTC",
    )
    assert schedule.is_armed(datetime(2026, 10, 19, 22, 30, tzinfo=UTC)) is True
    assert schedule.is_armed(datetime(2026, 10, 20, 1, 30, tzinfo=UTC)) is False
    assert schedule.is_armed(datetime(2026, 10, 20, 3, 0, tzinfo=UTC)) is False

    overridden = ArmingSchedule(
        "stream-1",
        weekly=weekly,
        default_armed=False,
        override_armed=True,
        override_until=datetime(2026, 10, 20, 4, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert overridden.is_armed(datetime(2026, 10, 20, 3, 0, tzinfo=UTC)) is True
    assert overridden.is_armed(datetime(2026, 10, 20, 4, 0, tzinfo=UTC)) is False


def test_schedule_round_trips_through_sqlite(tmp_path):
    schedule = ArmingSchedule(
        "stream-1",
        weekly=((TimeWindow(8 * 60, 18 * 60),), (), (), (), (), (), ()),
        holidays=frozenset({"2026-12-25"}),
        default_armed=False,
        override_armed=False,
        override_until=datetime.now(UTC) + timedelta(hours=1),
    )
    store = SQLiteStore(tmp_path / "schedule.db")
    store.save_arming_schedule(schedule, actor="operator")
    loaded = store.get_arming_schedule("stream-1")
    assert loaded is not None
    assert loaded.as_dict() == schedule.as_dict()
    store.close()


def test_schedule_evaluates_in_configured_timezone():
    schedule = ArmingSchedule(
        "stream-tz",
        weekly=((TimeWindow(9 * 60, 10 * 60),), (), (), (), (), (), ()),
        default_armed=False,
        timezone="Asia/Shanghai",
    )
    # 01:30 UTC is 09:30 in Shanghai and therefore inside the configured window.
    assert schedule.is_armed(datetime(2026, 1, 5, 1, 30, tzinfo=UTC)) is True
    assert schedule.is_armed(datetime(2026, 1, 5, 0, 30, tzinfo=UTC)) is False


def test_schedule_editor_time_parser_round_trips_and_rejects_invalid_ranges():
    windows = parse_windows("08:00-18:00, 22:30-02:15")
    assert format_windows(windows) == "08:00-18:00, 22:30-02:15"
    assert parse_windows("08:00-18:00，22:30-02:15") == windows
    try:
        parse_windows("25:00-26:00")
    except ValueError as exc:
        assert "超出范围" in str(exc)
    else:
        raise AssertionError("invalid schedule time was accepted")
