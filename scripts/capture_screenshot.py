from playwright.sync_api import sync_playwright

def capture(url, output_path, viewport_width=1920, viewport_height=1080):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': viewport_width, 'height': viewport_height})
        page.goto(url, wait_until='networkidle')
        page.screenshot(path=output_path, full_page=False)
        browser.close()

if __name__ == "__main__":
    url = "http://31.97.142.91:5055/clients/denzo-studios/pages/33/preview"

    capture(url, "/root/denzo-seo/screenshots/denzo_studios_page33_desktop.png", 1920, 1080)
    print("Desktop screenshot saved.")

    capture(url, "/root/denzo-seo/screenshots/denzo_studios_page33_mobile.png", 375, 812)
    print("Mobile screenshot saved.")
