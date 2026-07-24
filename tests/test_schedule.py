"""Tests für die reine Zeitfenster-Logik (app.loadmanager.schedule)."""

from datetime import datetime, time

from app.loadmanager.schedule import ScheduleWindow, is_blocked

# 2026-07-20 ist ein Montag (weekday()==0), 2026-07-21 ein Dienstag.
MONDAY = datetime(2026, 7, 20)
TUESDAY = datetime(2026, 7, 21)
SUNDAY = datetime(2026, 7, 19)


def _dt(base: datetime, hour: int, minute: int = 0) -> datetime:
    return base.replace(hour=hour, minute=minute)


def test_no_windows_never_blocks():
    assert is_blocked([], _dt(MONDAY, 23)) is False


def test_same_day_window_blocks_inside_and_not_outside():
    # Montag 08:00-12:00, nur an Montagen (Bit 0)
    w = ScheduleWindow(weekdays_mask=0b0000001, start_time=time(8, 0), end_time=time(12, 0))
    assert is_blocked([w], _dt(MONDAY, 9)) is True
    assert is_blocked([w], _dt(MONDAY, 12)) is False  # Ende exklusiv
    assert is_blocked([w], _dt(MONDAY, 7, 59)) is False
    assert is_blocked([w], _dt(TUESDAY, 9)) is False  # anderer Wochentag


def test_overnight_window_blocks_late_evening_on_start_day():
    # Montag 22:00 - Dienstag 06:00, nur an Montagen (Bit 0)
    w = ScheduleWindow(weekdays_mask=0b0000001, start_time=time(22, 0), end_time=time(6, 0))
    assert is_blocked([w], _dt(MONDAY, 23)) is True
    assert is_blocked([w], _dt(MONDAY, 21, 59)) is False


def test_overnight_window_blocks_early_morning_on_following_day():
    # Der frühe Teil (nach Mitternacht) gehört noch zum Montags-Fenster,
    # auch wenn "jetzt" bereits Dienstag ist (Regressionstest für den
    # ursprünglichen Mitternachts-Wrap-Bug).
    w = ScheduleWindow(weekdays_mask=0b0000001, start_time=time(22, 0), end_time=time(6, 0))
    assert is_blocked([w], _dt(TUESDAY, 5)) is True
    assert is_blocked([w], _dt(TUESDAY, 6)) is False  # Ende exklusiv
    assert is_blocked([w], _dt(TUESDAY, 7)) is False


def test_overnight_window_not_blocked_when_previous_day_not_in_mask():
    # Fenster gilt nur an Sonntagen (Bit 6) -> Montagfrüh nicht gesperrt,
    # obwohl die Uhrzeit ins Fenster fallen würde.
    w = ScheduleWindow(weekdays_mask=0b1000000, start_time=time(22, 0), end_time=time(6, 0))
    assert is_blocked([w], _dt(SUNDAY, 23)) is True
    assert is_blocked([w], _dt(MONDAY, 5)) is True  # Vortag (Sonntag) hat Bit gesetzt
    assert is_blocked([w], _dt(TUESDAY, 5)) is False  # Vortag (Montag) hat Bit nicht gesetzt


def test_multiple_windows_any_match_blocks():
    w1 = ScheduleWindow(weekdays_mask=0b0000001, start_time=time(8, 0), end_time=time(9, 0))
    w2 = ScheduleWindow(weekdays_mask=0b0000001, start_time=time(20, 0), end_time=time(21, 0))
    assert is_blocked([w1, w2], _dt(MONDAY, 20, 30)) is True
    assert is_blocked([w1, w2], _dt(MONDAY, 10)) is False
