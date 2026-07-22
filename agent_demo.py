import json
from typing import Any

from playwright.sync_api import sync_playwright

from main import click_link, list_links, open_page, inspect_page


USER_REQUEST = "Example sitesini aç ve Learn more bağlantısına git."


def fake_llm_decision(step: int) -> dict[str, Any]:
    """
    Gerçek LLM yerine geçici olarak sabit kararlar üretir.
    """

    if step == 0:
        return {
            "tool": "open_page",
            "arguments": {
                "url": "https://example.com",
            },
        }

    if step == 1:
        return {
            "tool": "inspect_page",
            "arguments": {},
    }

    if step == 2:
        return {
       
            "tool": "click_link",
            "arguments": {
                "link_name": "Learn more",
            },
        }

    return {
        "tool": "finish",
        "arguments": {},
    }

def execute_tool(page, decision: dict[str, Any]) -> Any:
    """LLM kararına göre uygun Playwright aracını çalıştırır."""

    tool_name = decision["tool"]
    arguments = decision.get("arguments", {})

    if tool_name == "open_page":
        return open_page(
            page,
            arguments["url"],
        )

    if tool_name == "list_links":
        return list_links(page)
    
    if tool_name == "inspect_page":
        return inspect_page(page)

    if tool_name == "click_link":
        return click_link(
            page,
            arguments["link_name"],
        )
    


    raise ValueError(f"Bilinmeyen araç: {tool_name}")


def main() -> None:
    print(f"Kullanıcı isteği: {USER_REQUEST}\n")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            for step in range(4):
                decision = fake_llm_decision(step)

                print("LLM kararı:")
                print(
                    json.dumps(
                        decision,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

                tool_name = decision["tool"]
                arguments = decision["arguments"]

                if tool_name == "finish":
                    print("\nGörev tamamlandı.")
                    break

                observation = execute_tool(page, decision)

                print("\nPlaywright sonucu:")
                print(
                    json.dumps(
                        observation,
                        ensure_ascii=False,
                        indent=2,
                    )
                )

                print("-" * 50)

        finally:
            browser.close()


if __name__ == "__main__":
    main()