#!/usr/bin/env python3
"""UI test: every visible price must live-tick when a Binance tick arrives.

Binance websockets are geo-blocked from CI/VMs, so instead of waiting for a
real stream we call the page's own applyTick() with synthetic data and assert
the DOM (home card price, 24h %, market table row) updates and flashes.
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
                subscribedKey: liveTicker.key.split(',').length,
            })"""
        )
        print("state:", state)
        assert state["tickEls"] > 0, "no data-tick elements rendered"
        assert state["indexed"] > 0, "tickIndex empty — syncLiveTicker not run"
        assert state["subscribedKey"] >= state["indexed"], "subscription set too small"

        symbol = page.evaluate("() => [...tickIndex.keys()][0]")
        before = page.evaluate(
            """(sym) => {
                const idx = tickIndex.get(sym);
                return { price: idx.price[0].textContent, pct: idx.pct.length ? idx.pct[0].textContent : null };
            }""",
            symbol,
        )

        page.evaluate(
            "(sym) => applyTick(sym, 123456.78, 100000)",
            symbol,
        )
        page.wait_for_timeout(300)

        after = page.evaluate(
            """(sym) => {
                const idx = tickIndex.get(sym);
                const el = idx.price[0];
                return {
                    price: el.textContent,
                    flashed: el.classList.contains('tick'),
                    pct: idx.pct.length ? idx.pct[0].textContent : null,
                    pctTone: idx.pct.length ? idx.pct[0].className : null,
                };
            }""",
            symbol,
        )
        print(f"{symbol}: {before} -> {after}")
        assert after["price"] != before["price"], "price cell did not update"
        assert "123,456" in after["price"], after["price"]
        assert after["flashed"], "no flash animation class"
        if after["pct"] is not None:
            assert "23.4" in after["pct"], after["pct"]  # (123456.78-100000)/100000 = +23.46%
            assert "pos" in (after["pctTone"] or ""), after["pctTone"]

        # Market tab rows must tick too.
        page.click(".tab-btn[data-tab='market']")
        page.evaluate("(sym) => applyTick(sym, 111111.11, 100000)", symbol)
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
    print("OK — all visible prices tick live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
