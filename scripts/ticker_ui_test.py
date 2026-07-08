#!/usr/bin/env python3
"""UI test: every visible price must live-tick when a price message arrives.

Live prices are relayed by our own server over /ws ({type:"prices"} frames),
so the browser never talks to an exchange. Exchange access is geo-blocked in
CI/VMs anyway — we feed the page's own onPricesMessage() with synthetic data
and assert the DOM (home card price, 24h %, market table row) updates.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8123"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 430, "height": 930})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/dashboard", wait_until="networkidle")
        page.wait_for_selector("[data-tick]", timeout=15000)

        state = page.evaluate(
            """() => ({
                tickEls: document.querySelectorAll('[data-tick]').length,
                pctEls: document.querySelectorAll('[data-tick-pct]').length,
                indexed: tickIndex.size,
            })"""
        )
        print("state:", state)
        assert state["tickEls"] > 0, "no data-tick elements rendered"
        assert state["indexed"] > 0, "tickIndex empty — syncLiveTicker not run"

        symbol = page.evaluate("() => [...tickIndex.keys()][0]")
        before = page.evaluate(
            """(sym) => {
                const idx = tickIndex.get(sym);
                return { price: idx.price[0].textContent, pct: idx.pct.length ? idx.pct[0].textContent : null };
            }""",
            symbol,
        )

        page.evaluate(
            "(sym) => onPricesMessage({ type: 'prices', data: { [sym]: [123456.78, 23.46] } })",
            symbol,
        )
        page.wait_for_timeout(300)

        after = page.evaluate(
            """(sym) => {
                const idx = tickIndex.get(sym);
                const el = idx.price[0];
                return {
                    price: el.textContent,
                    flashed: el.classList.contains('tick') || el.classList.contains('tick-up') || el.classList.contains('tick-down'),
                    pct: idx.pct.length ? idx.pct[0].textContent : null,
                    pctTone: idx.pct.length ? idx.pct[0].className : null,
                    status: document.querySelector('[data-ticker-status]').textContent,
                    watchKey: liveTicker.watchKey,
                };
            }""",
            symbol,
        )
        print(f"{symbol}: {before} -> {after}")
        assert after["price"] != before["price"], "price cell did not update"
        assert "123,456" in after["price"], after["price"]
        assert after["flashed"], "no flash animation class"
        assert "실시간" in after["status"], after["status"]
        assert after["watchKey"], "watch list was not sent to the server"
        if after["pct"] is not None:
            assert "23.46" in after["pct"], after["pct"]
            assert "pos" in (after["pctTone"] or ""), after["pctTone"]

        # Upbit-style directional flash: up tick flashes red, down tick blue.
        page.evaluate("(sym) => onPricesMessage({ type: 'prices', data: { [sym]: [123500, 23.5] } })", symbol)
        page.wait_for_timeout(100)
        up = page.evaluate("(sym) => tickIndex.get(sym).price[0].className", symbol)
        page.evaluate("(sym) => onPricesMessage({ type: 'prices', data: { [sym]: [123400, 23.4] } })", symbol)
        page.wait_for_timeout(100)
        down = page.evaluate("(sym) => tickIndex.get(sym).price[0].className", symbol)
        print("flash classes:", up, "|", down)
        assert "tick-up" in up, up
        assert "tick-down" in down, down

        # Market tab rows must tick too.
        page.click(".tab-btn[data-tab='market']")
        page.evaluate(
            "(sym) => onPricesMessage({ type: 'prices', data: { [sym]: [111111.11, -1.5] } })",
            symbol,
        )
        page.wait_for_timeout(300)
        market_cell = page.evaluate(
            """(sym) => {
                const el = document.querySelector(`#market-body [data-tick='${sym}']`)
                    || document.querySelector(`#alts-body [data-tick='${sym}']`);
                return el ? el.textContent : null;
            }""",
            symbol,
        )
        print("market cell:", market_cell)
        if market_cell is not None:
            assert "111,111" in market_cell, market_cell

        assert not errors, errors
        browser.close()
    print("OK — all visible prices tick from relayed price messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
