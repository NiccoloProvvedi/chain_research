"""The real interface in the project's Chromium, on the real server (only the launch of the investigation process is
fake)."""
import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from chain_research import configuration, server
from chain_research.investigation import Investigation

pytestmark = pytest.mark.browser
TOKEN = "t" * 64


class App:
    def __init__(self, handler: server.Manager, port_number: int, page: Page, problems: list[str]) -> None:
        self.handler, self.port_number, self.page, self.problems = handler, port_number, page, problems
        self.started: list[str] = []

    def fresh(self, **status) -> Investigation:
        ind = Investigation.create("The test story", base_folder=self.handler.folder)
        ind.status.update(status)
        ind.save()
        return ind

    def open_page(self) -> None:
        self.page.goto(f"http://127.0.0.1:{self.port_number}/")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[App]:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(configuration.folder("engines_folder"))
    handler = server.Manager(tmp_path / "investigations")
    handler.folder.mkdir()
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port_number = listener.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.create_app(TOKEN, lambda: port_number, handler), log_level="warning"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.02)
    with sync_playwright() as p:
        chromium = p.chromium.launch(channel="chromium")
        context = chromium.new_context()
        context.add_cookies([{"name": "token", "value": TOKEN, "url": f"http://127.0.0.1:{port_number}"}])
        page = context.new_page()
        problems: list[str] = []
        # The browser asks for the icon on its own: the app has none.
        page.on("console", lambda m: problems.append(f"{m.text} ({m.location.get('url')})")
                  if m.type == "error" and not m.location.get("url", "").endswith("/favicon.ico") else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        a = App(handler, port_number, page, problems)
        monkeypatch.setattr(handler, "launch", lambda ident, document_only=False: a.started.append(ident))
        yield a
        chromium.close()
    srv.should_exit = True
    thread.join(10)


def test_page_without_errors_and_new_investigation(app: App) -> None:
    app.open_page()
    expect(app.page.locator("#list-empty")).to_be_visible()
    app.page.fill("#topic", "History of Arduino")
    app.page.click("#start")
    expect(app.page.locator("#list li")).to_have_count(1)
    assert len(app.started) == 1 and app.problems == []


def test_old_end_does_not_stop_live_view(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    ind = app.fresh(status="in_progress", phase="Working", plan=None)
    ind.event({"kind": "end", "status": "stopped", "reason": "past run"})
    ind.event({"kind": "start", "director_model": "sonnet", "topic": "x", "limits": {}})
    monkeypatch.setattr(app.handler, "in_execution", lambda ident: True)
    app.open_page()
    activity = app.page.locator("#v-activity")
    expect(activity).to_contain_text("past run")
    ind.event({"kind": "phase", "text": "event-after-resume"})
    expect(activity).to_contain_text("event-after-resume")
    assert app.problems == []


def test_buttons_follow_status(app: App) -> None:
    app.fresh(status="finished", end_reason="enough", document={"pdf": "x.pdf"})
    app.open_page()
    expect(app.page.locator("#v-status")).to_have_text("finished")
    expect(app.page.locator("#b-open")).to_be_visible()
    expect(app.page.locator("#b-resume")).to_be_hidden()
    expect(app.page.locator("#b-stop")).to_be_hidden()


def test_resume_with_new_limits(app: App) -> None:
    ind = app.fresh(status="stopped", end_reason="budget")
    app.open_page()
    expect(app.page.locator("#b-resume")).to_be_visible()
    app.page.click("#b-resume")
    expect(app.page.locator("#r-budget")).to_have_value("5")
    app.page.fill("#r-budget", "11")
    app.page.click("#r-confirm")
    expect(app.page.locator("#resume")).to_be_hidden()
    assert app.started == [ind.folder.name]
    assert json.loads((ind.folder / "status.json").read_text())["limits"]["budget_usd"] == 11
    assert app.problems == []


def test_without_limits_asks_confirmation(app: App) -> None:
    app.open_page()
    app.page.fill("#topic", "History of Arduino")
    app.page.check("#unlimited")
    app.page.once("dialog", lambda d: d.dismiss())
    app.page.click("#start")
    app.page.wait_for_timeout(300)
    assert app.started == []
    app.page.once("dialog", lambda d: d.accept())
    app.page.click("#start")
    expect(app.page.locator("#list li")).to_have_count(1)
    assert len(app.started) == 1


def test_advanced_settings_saved(app: App) -> None:
    app.open_page()
    app.page.click("#open-settings")
    expect(app.page.locator("#s-question-pages")).to_have_value("30")
    app.page.fill("#s-question-pages", "12")
    app.page.click("#s-save")
    expect(app.page.locator("#settings")).to_be_hidden()
    assert configuration.load()["question_pages"] == 12 and app.problems == []


def test_review_model_in_settings(app: App) -> None:
    app.open_page()
    app.page.click("#open-settings")
    expect(app.page.locator("#s-review-model")).to_have_value("sonnet")
    app.page.select_option("#s-review-model", "opus")
    app.page.click("#s-save")
    expect(app.page.locator("#settings")).to_be_hidden()
    assert configuration.load()["review_model"] == "opus" and app.problems == []


def test_status_of_each_answer_shown(app: App) -> None:
    def d(n: int, **other) -> dict:
        return {"id": f"d{n}", "round": 1, "section": "s1", "question": f"Question {n}", "status": "answer",
                "answer": f"**Answer:** value {n} [1].\n\n**Confidence:** high — from the model", "sources": [], **other}
    app.fresh(status="finished", plan={"title": "T", "goal": "G", "sections": [{"id": "s1", "title": "One", "purpose": "x"}]},
              questions=[d(1, answer_status="verified", status_reasons=[]),
                       d(2, answer_status="uncertain", status_reasons=["1 chain links not confirmed"]),
                       d(3)])
    app.open_page()
    app.page.click("#list li")
    rows = app.page.locator("#v-questions .question")
    expect(rows).to_have_count(3)
    expect(rows.nth(0).locator(".chip.verified")).to_have_text("verified")
    expect(rows.nth(1).locator(".chip.uncertain")).to_have_text("uncertain")
    expect(rows.nth(1).locator(".info")).to_contain_text("1 chain links not confirmed")
    expect(rows.nth(1).locator(".info")).not_to_contain_text("confidence")
    expect(rows.nth(2).locator(".info")).to_contain_text("confidence high")  # older investigation: the model's line stays
    assert app.problems == []
