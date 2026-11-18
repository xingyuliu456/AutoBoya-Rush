from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from autoboya.bykc import BykcClient
from autoboya.exceptions import (
    AlreadySelected,
    BoyaApiError,
    CourseFull,
    CourseNotOpen,
    SelectionLimitReached,
    SessionExpired,
)
from autoboya.models import BoyaCourse
from autoboya.rules import is_selectable, parse_dt

RETRY_INTERVAL = 0.4
START_LEAD = timedelta(seconds=2)
SESSION_CHECK_LEAD = timedelta(seconds=25)


@dataclass(frozen=True)
class RushCandidate:
    course: BoyaCourse
    start: datetime
    end: datetime


def rush_candidates(courses: list[BoyaCourse], now: datetime) -> list[RushCandidate]:
    candidates = []
    for course in courses:
        start = parse_dt(course.select_start)
        end = parse_dt(course.select_end)
        if course.id > 0 and start and end and is_selectable(course, max(now, start)):
            candidates.append(RushCandidate(course, start, end))
    return sorted(candidates, key=lambda candidate: candidate.start)


def rush_course(
    candidate: RushCandidate,
    client: BykcClient,
    reconnect: Callable[[], BykcClient],
    report: Callable[[str], None],
) -> bool:
    course = candidate.course
    begin = candidate.start - START_LEAD
    check_at = candidate.start - SESSION_CHECK_LEAD
    checked = False
    while (now := datetime.now()) < begin:
        if not checked and now >= check_at:
            try:
                client.get_all_config()
            except SessionExpired:
                report("会话已过期，正在重新登录。")
                client = reconnect()
            except httpx.TransportError as exc:
                report(f"开抢前会话校验遇到网络异常，开抢时继续尝试。{exc}")
            checked = True
            continue
        report(f"等待开抢，距选课开放还有 {max(1, int((candidate.start - now).total_seconds()))} 秒。")
        wake_at = begin if checked else check_at
        time.sleep(min(20.0, (wake_at - now).total_seconds()))

    if datetime.now() <= candidate.end:
        report(f"开始尝试选课。课程 {course.id} {course.name}")
    reauthenticated = False
    while datetime.now() <= candidate.end:
        try:
            client.select_course(course.id)
        except AlreadySelected:
            report("该课程已选中。")
            return True
        except (CourseFull, SelectionLimitReached) as exc:
            report(f"已停止选课。{exc}")
            return False
        except SessionExpired:
            if reauthenticated:
                raise
            report("会话已失效，正在重新登录。")
            client = reconnect()
            reauthenticated = True
        except (CourseNotOpen, httpx.TransportError) as exc:
            reauthenticated = False
            report(f"稍后重试。{exc}")
        except BoyaApiError as exc:
            if any(reason in exc.errmsg for reason in ("冲突", "上限", "不符合", "不可", "已选")):
                report(f"已停止选课。{exc.errmsg}")
                return False
            reauthenticated = False
            report(f"稍后重试。{exc}")
        except httpx.HTTPStatusError as exc:
            if not 500 <= exc.response.status_code < 600:
                raise
            reauthenticated = False
            report(f"服务器返回 HTTP {exc.response.status_code}，稍后重试。")
        else:
            report("选课成功。")
            return True
        time.sleep(RETRY_INTERVAL)

    report("选课窗口已结束，已停止尝试。")
    return False
