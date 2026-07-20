from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def inspect_page(url: str) -> None:
    """Open a page and print its basic information and links."""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=30_000,
            )

            print(f"URL: {page.url}")
            print(f"Başlık: {page.title()}")

            if response is not None:
                print(f"HTTP durum kodu: {response.status}")

            links = page.locator("a").evaluate_all(
                """
                elements => elements.map(element => ({
                    text: element.innerText.trim(),
                    href: element.href
                }))
                """
            )

            print(f"\nBulunan bağlantı sayısı: {len(links)}")

            for index, link in enumerate(links, start=1):
                print(f"{index}. {link['text'] or '(metinsiz bağlantı)'}")
                print(f"   {link['href']}")

            if links:
                print("\nİlk bağlantıya tıklanıyor...")

                page.get_by_role("link", name="Learn more").click()
                page.wait_for_load_state("domcontentloaded")

                print(f"Yeni URL: {page.url}")
                print(f"Yeni sayfa başlığı: {page.title()}")

                page.screenshot(
                    path="artifacts/after_click.png",
                    full_page=True,
                )

                print("Yeni ekran görüntüsü: artifacts/after_click.png")

            page.screenshot(
                path="artifacts/page.png",
                full_page=True,
            )

            print("\nEkran görüntüsü: artifacts/page.png")

        except PlaywrightError as error:
            print(f"Playwright hatası: {error}")

        finally:
            browser.close()


if __name__ == "__main__":
    inspect_page("https://example.com")