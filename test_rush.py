from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import autoboya_rush as cli
import rush_logic as rush
from autoboya import session
from autoboya.auth import AuthSession, CaptchaChallenge
from autoboya.cache import CourseCache
from autoboya.exceptions import BoyaApiError, CaptchaRequired, CourseFull, CourseNotOpen, SessionExpired
from autoboya.models import BoyaCourse, UserRecord


def freeze_rush_clock(monkeypatch, initial_time):
    class Clock:
        current = initial_time

        @classmethod
        def now(cls):
            return cls.current

        @classmethod
        def sleep(cls, seconds):
            cls.current += timedelta(seconds=seconds)

    monkeypatch.setattr(cli, "datetime", Clock)
    monkeypatch.setattr(rush, "datetime", Clock)
    monkeypatch.setattr(rush, "time", SimpleNamespace(sleep=Clock.sleep))
    return Clock


@pytest.mark.parametrize("use_user_option", [False, True])
def test_rush_logs_in_prompts_for_captcha_and_waits_for_numbered_course(monkeypatch, tmp_path, use_user_option):
    monkeypatch.setenv("AUTOBOYA_HOME", str(tmp_path))
    clock = freeze_rush_clock(monkeypatch, datetime(2026, 11, 18, 9, 59, 30))
    start = datetime(2026, 11, 18, 10)
    end = datetime(2026, 11, 18, 11)
    store = cli.AutoBoyaStore()
    store.init()
    store.upsert_user(UserRecord("another-student"))
    store.upsert_user(UserRecord("student"))
    session.save_session(store, AuthSession("student", "old-token", []))
    monkeypatch.setattr(session, "password_for", lambda store, user: "saved-password")
    events = []
    image = tmp_path / "captcha.png"
    opened_images = []
    monkeypatch.setattr(cli.os, "startfile", opened_images.append, raising=False)

    open_course = BoyaCourse(100, "现在可选", category="美育", select_start="2026-11-18 09:00:00", select_end=str(end))
    future_course = BoyaCourse(200, "稍后开放", category="劳育", select_start=str(start), select_end=str(end))
    full_course = BoyaCourse(300, "人数已满", select_start=str(start), select_end=str(end), current_count=1, max_count=1)
    selected_course = BoyaCourse(400, "已经选择", select_start=str(start), select_end=str(end), selected=True)
    expired_course = BoyaCourse(500, "已经关闭", select_start="2026-11-18 09:00:00", select_end="2026-11-18 09:30:00")

    class FakeAuthClient:
        def __init__(self, store):
            pass

        def preflight_login(self):
            raise CaptchaRequired(CaptchaChallenge("challenge", "image", image, "execution"))

        def login(self, user, password, captcha):
            assert (user, password, captcha) == ("student", "saved-password", "1234")
            events.append("login")
            return AuthSession(user, "fresh-token", [])

    class FakeBykcClient:
        def __init__(self, token, cookies):
            assert token == "fresh-token"
            self.client = SimpleNamespace(close=lambda: None)

        def query_courses(self):
            events.append("courses")
            return [future_course, open_course, full_course, selected_course, expired_course]

        def get_all_config(self):
            events.append(("config", clock.now()))
            return {"data": {"semester": [{"semesterStartDate": "2026-09-01", "semesterEndDate": "2027-01-31"}]}}

        def select_course(self, course_id):
            assert course_id == future_course.id
            events.append(("select", clock.now()))
            if clock.now() < start:
                raise CourseNotOpen("未到选课时间")

        def query_chosen_courses(self, start, end):
            events.append("chosen")
            return [future_course]

        def query_statistics(self):
            events.append("statistics")
            return {
                "validCount": 1,
                "statistical": {"60|博雅课程": {"55|德育": {"assessmentCount": 4, "selectAssessmentCount": 2}}},
            }

    monkeypatch.setattr(session, "AuthClient", FakeAuthClient)
    monkeypatch.setattr(session, "BykcClient", FakeBykcClient)
    args = ["--user", "student"] if use_user_option else []
    user_input = "" if use_user_option else "student\n"
    result = CliRunner().invoke(cli.app, args, input=user_input + "1234\n9\n2\n")

    assert result.exit_code == 0, result.output
    assert ("请输入学号" in result.output) is (not use_user_option)
    assert str(image) in result.output
    if cli.sys.platform == "win32":
        assert opened_images == [str(image)]
    assert all(name in result.output for name in ("现在可选", "稍后开放", "未开抢", "选课成功"))
    assert all(name not in result.output for name in ("人数已满", "已经选择", "已经关闭"))
    assert "请输入 1 到 2" in result.output
    statistics_output, course_output = result.output.split("课程类型", 1)
    assert "博雅要求与完成情况" in statistics_output
    assert any("德育" in row and [cell.strip() for cell in row.split("│")[2:4]] == ["4", "2"] for row in statistics_output.splitlines())
    assert "美育" in course_output
    assert "劳育" in course_output
    assert events[:4] == ["login", "courses", "statistics", ("config", start - timedelta(seconds=25))]
    attempts = [event[1] for event in events if isinstance(event, tuple) and event[0] == "select"]
    assert len(attempts) == 6
    assert attempts[0] == start - timedelta(seconds=2)
    assert attempts[1] - attempts[0] == timedelta(seconds=0.4)
    assert events[-2:] == ["chosen", "statistics"]
    assert CourseCache(store).load_selected("student")[0]["id"] == 200


def test_rush_reconnects_on_expiry_and_stops_on_full(monkeypatch):
    now = datetime(2026, 11, 18, 10)
    freeze_rush_clock(monkeypatch, now)
    candidate = rush.RushCandidate(BoyaCourse(100), now, now + timedelta(minutes=1))
    calls = []

    def expire(course_id):
        calls.append(("expired", course_id))
        raise SessionExpired("会话已失效")

    def full(course_id):
        calls.append(("full", course_id))
        raise CourseFull("名额已满")

    assert not rush.rush_course(
        candidate, SimpleNamespace(select_course=expire), lambda: SimpleNamespace(select_course=full), lambda _: None
    )
    assert calls == [("expired", 100), ("full", 100)]


def test_rush_does_not_submit_again_after_window_closes_during_relogin(monkeypatch):
    now = datetime(2026, 11, 18, 10)
    clock = freeze_rush_clock(monkeypatch, now)
    candidate = rush.RushCandidate(BoyaCourse(100), now, now + timedelta(seconds=1))
    calls = []

    def expire(course_id):
        calls.append(course_id)
        raise SessionExpired("会话已失效")

    def reconnect():
        clock.sleep(2)
        return SimpleNamespace(select_course=calls.append)

    assert not rush.rush_course(candidate, SimpleNamespace(select_course=expire), reconnect, lambda _: None)
    assert calls == [100]


def test_rush_explains_how_to_configure_an_unregistered_student(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOBOYA_HOME", str(tmp_path))

    def unexpected_login(*args, **kwargs):
        pytest.fail("Login should wait until the entered student ID is registered")

    monkeypatch.setattr(cli, "force_login_bykc_client", unexpected_login)
    result = CliRunner().invoke(cli.app, input="unregistered-student\n")

    assert result.exit_code == 2
    assert "python -m autoboya user add" in result.output


def test_rush_shows_courses_when_statistics_are_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOBOYA_HOME", str(tmp_path))
    now = datetime(2026, 11, 18, 10)
    freeze_rush_clock(monkeypatch, now)
    store = cli.AutoBoyaStore()
    store.init()
    store.upsert_user(UserRecord("student"))

    def query_statistics():
        raise BoyaApiError("unavailable", "统计暂时不可用")

    client = SimpleNamespace(
        client=SimpleNamespace(close=lambda: None),
        query_courses=lambda: [
            BoyaCourse(100, "测试课程", select_start=str(now), select_end=str(now + timedelta(hours=1)))
        ],
        query_statistics=query_statistics,
    )
    monkeypatch.setattr(cli, "force_login_bykc_client", lambda *args, **kwargs: client)
    result = CliRunner().invoke(cli.app, ["--user", "student"], input="0\n")

    assert result.exit_code == 0, result.output
    assert "暂时无法读取博雅统计" in result.output
    assert "测试课程" in result.output
    assert "课程序号" in result.output
