import argparse
import json

from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

INTERACTIVE_SELECTOR = """
a:visible,
button:visible,
input:visible,
textarea:visible,
select:visible,
[role="button"]:visible,
[role="link"]:visible,
[contenteditable="true"]:visible
"""


def open_page(page: Page, url: str) -> dict[str, Any]:
    """Verilen URL'yi açar ve sayfanın temel bilgilerini döndürür."""

    response = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=30_000,
    )

    return {
        "url": page.url,
        "title": page.title(),
        "status": response.status if response is not None else None,
    }


def list_links(page: Page) -> list[dict[str, str]]:
    """Sayfadaki bağlantıların görünen metinlerini ve adreslerini döndürür."""

    return page.locator("a").evaluate_all(
        """
        elements => elements.map(element => ({
            text: element.innerText.trim(),
            href: element.href
        }))
        """
    )


def inspect_page(page: Page) -> dict[str, Any]:
    """
    Sayfanın görünen başlıklarını ve etkileşimli elementlerini
    LLM'nin anlayabileceği yapılandırılmış biçimde döndürür.
    """

    headings = [
        text.strip()
        for text in page.locator(
            "h1:visible, h2:visible, h3:visible"
        ).all_inner_texts()
        if text.strip()
    ]

    elements = page.locator(INTERACTIVE_SELECTOR).evaluate_all(
        """
        elements => elements.map((element, index) => {
            const id = `e${index + 1}`;

            element.setAttribute("data-agent-id", id);

            const tag = element.tagName.toLowerCase();
            const role = element.getAttribute("role") || "";
            const inputType = element.getAttribute("type") || "";

            let elementType;

            if (tag === "a" || role === "link") {
                elementType = "link";
            } else if (
                tag === "button" ||
                role === "button" ||
                ["button", "submit", "reset"].includes(inputType)
            ) {
                elementType = "button";
            } else if (tag === "select") {
                elementType = "select";
            } else if (tag === "textarea") {
                elementType = "textarea";
            } else if (tag === "input") {
                elementType = "input";
            } else if (element.isContentEditable) {
                elementType = "contenteditable";
            } else {
                elementType = tag;
            }

            const label = element.labels
                ? Array.from(element.labels)
                    .map(labelElement => labelElement.innerText.trim())
                    .filter(Boolean)
                    .join(" | ")
                : "";

            const visibleText = (
                element.innerText ||
                element.value ||
                ""
            ).trim();

            const name = (
                label ||
                element.getAttribute("aria-label") ||
                element.getAttribute("placeholder") ||
                visibleText ||
                element.getAttribute("name") ||
                element.getAttribute("title") ||
                ""
            ).trim();

            return {
                id: id,
                element_type: elementType,
                tag: tag,
                role: role,
                input_type: inputType,
                name: name,
                text: visibleText,
                href: element.href || "",
                placeholder: element.getAttribute("placeholder") || "",
                value: element.value || "",
                disabled: Boolean(element.disabled),
                readonly: Boolean(element.readOnly)
            };
        }).filter(item => item.name || item.href)
        """
    )

    return {
        "url": page.url,
        "title": page.title(),
        "headings": headings,
        "elements": elements,
    }


def click_link(page: Page, link_name: str) -> dict[str, str]:
    """Görünen adına göre bir bağlantıya tıklar."""

    page.get_by_role(
        "link",
        name=link_name,
        exact=True,
    ).click()

    page.wait_for_load_state("domcontentloaded")

    return {
        "url": page.url,
        "title": page.title(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bir web sayfasını Playwright ile incele."
    )

    parser.add_argument(
        "--url",
        required=True,
        help="Açılacak web sayfasının adresi.",
    )

    parser.add_argument(
        "--link",
        required=False,
        help="Tıklanacak bağlantının görünen metni.",
    )

    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        result: dict[str, Any] = {}

        try:
            result["opened_page"] = open_page(page, args.url)
            result["page"] = inspect_page(page)

            page.screenshot(
                path="artifacts/before_click.png",
                full_page=True,
            )

            if args.link:
                result["clicked_page"] = click_link(page, args.link)

                page.screenshot(
                    path="artifacts/after_click.png",
                    full_page=True,
                )

            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )
            )

        except PlaywrightError as error:
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        finally:
            browser.close()


if __name__ == "__main__":
    main()