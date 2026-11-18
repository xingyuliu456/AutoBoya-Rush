from __future__ import annotations

import os
import sys
from datetime import datetime

import httpx
import typer
from rich.console import Console
from rich.table import Table

from autoboya.auth import CaptchaChallenge
from autoboya.bykc import BykcClient
from autoboya.cache import CourseCache
from autoboya.cli import HELP_CONTEXT, current_semester_window, fail_command, mask, print_stats_table
from autoboya.exceptions import AutoBoyaError, BoyaApiError, SessionExpired
from autoboya.session import call_with_reauth, force_login_bykc_client
from autoboya.storage import AutoBoyaStore

from rush_logic import RushCandidate, rush_candidates, rush_course

app = typer.Typer(
    add_completion=False,
    pretty_exceptions_show_locals=False,
    context_settings=HELP_CONTEXT,
)


@app.command(help="登录并刷新课程，按输入的序号定时尝试选课。")
def rush(
    username: str | None = typer.Option(None, "--user", help="已配置的学号，省略时交互输入。"),
) -> None:
    store = AutoBoyaStore()
    store.init()
    user = (typer.prompt("请输入学号") if username is None else username).strip()
    if store.get_user(user) is None:
        raise typer.BadParameter("此学号尚未配置。请先运行 python -m autoboya user add 你的学号。")
    active_client: BykcClient | None = None

    def connect() -> BykcClient:
        nonlocal active_client
        if active_client is not None:
            active_client.client.close()
            active_client = None
        typer.echo(f"正在登录 {mask(user)}…")
        active_client = force_login_bykc_client(store, user, captcha_provider=prompt_rush_captcha)
        typer.echo("登录成功，已连接博雅系统。")
        return active_client

    try:
        client = connect()
        typer.echo("正在刷新课程…")
        try:
            courses = client.query_courses()
        except SessionExpired:
            client = connect()
            courses = client.query_courses()
        CourseCache(store).save_courses(courses)
        try:
            try:
                statistics = client.query_statistics()
            except SessionExpired:
                client = connect()
                statistics = client.query_statistics()
        except (BoyaApiError, SessionExpired, httpx.HTTPError) as exc:
            typer.echo(f"暂时无法读取博雅统计，继续显示课程列表。{exc}", err=True)
        else:
            typer.echo("博雅要求与完成情况")
            print_stats_table(statistics, username=user)
        candidate = choose_rush_candidate(rush_candidates(courses, datetime.now()))
        if candidate is None:
            return
        course = candidate.course
        typer.echo(f"目标课程 {course.id} {course.name}，选课开放时间 {candidate.start:%Y-%m-%d %H:%M:%S}。")
        if course.cancel_end:
            typer.echo(f"退课截止时间 {course.cancel_end}。")
        if not rush_course(candidate, client, connect, typer.echo):
            raise typer.Exit(1)
        try:
            refresh_current_user_cache(store, user)
            typer.echo("已刷新已选课程和统计。")
        except (AutoBoyaError, httpx.HTTPError, OSError, ValueError) as exc:
            typer.echo(f"已选结果已由接口确认，刷新缓存失败。{exc}", err=True)
    except (AutoBoyaError, httpx.HTTPError, OSError) as exc:
        fail_command(exc)
    finally:
        if active_client is not None:
            active_client.client.close()


def prompt_rush_captcha(challenge: CaptchaChallenge) -> str:
    typer.echo(f"验证码图片 {challenge.image_path}")
    if sys.platform == "win32":
        try:
            os.startfile(str(challenge.image_path))
        except OSError:
            typer.echo("无法自动打开图片，请按上方路径手动打开。")
    return typer.prompt("请查看图片后在终端输入验证码", hide_input=True).strip()


def choose_rush_candidate(candidates: list[RushCandidate]) -> RushCandidate | None:
    if not candidates:
        typer.echo("没有未开抢或正在开放的未选课程（满员课程已过滤）。")
        return None
    table = Table("序号", "课程", "课程类型", "状态", "开放选课", "上课时间", "地点", "人数/容量")
    now = datetime.now()
    for index, candidate in enumerate(candidates, start=1):
        course = candidate.course
        current = "?" if course.current_count is None else str(course.current_count)
        capacity = "?" if course.max_count is None else str(course.max_count)
        table.add_row(
            str(index), course.name, course.category or "-", "未开抢" if now < candidate.start else "可选",
            candidate.start.strftime("%m-%d %H:%M:%S"), course.course_start or "-",
            course.location or "-", f"{current}/{capacity}",
        )
    Console(markup=False, highlight=False).print(table)
    while True:
        index = typer.prompt("课程序号（0 退出）", type=int)
        if index == 0:
            return None
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
        typer.echo(f"请输入 1 到 {len(candidates)} 之间的序号，或输入 0 退出。")


def refresh_current_user_cache(store: AutoBoyaStore, user: str) -> None:
    cache = CourseCache(store)

    def refresh(client: BykcClient) -> None:
        start, end = current_semester_window(client.get_all_config())
        cache.save_selected(user, client.query_chosen_courses(start, end))
        cache.save_statistics(user, client.query_statistics())

    call_with_reauth(store, user, refresh, captcha_provider=prompt_rush_captcha)


if __name__ == "__main__":
    app()
