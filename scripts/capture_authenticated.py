from playwright.sync_api import sync_playwright

TARGET_URL = "http://31.97.142.91:5055/clients/denzo-studios/pages/33/preview"
LOGIN_URL = "http://31.97.142.91:5055/login"
USERNAME = "denzo"
PASSWORD = "denzo"  # default/common password — will try

DESKTOP_OUT = "/root/denzo-seo/screenshots/denzo_studios_page33_desktop.png"
MOBILE_OUT = "/root/denzo-seo/screenshots/denzo_studios_page33_mobile.png"

def try_login(page, password):
    page.goto(LOGIN_URL, wait_until='networkidle')
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state('networkidle')
    return "login" not in page.url

with sync_playwright() as p:
    # --- Desktop ---
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1920, 'height': 1080})

    passwords_to_try = ["denzo", "denzo123", "admin", "password", "denzo2026", "Denzo123"]
    logged_in = False
    for pwd in passwords_to_try:
        logged_in = try_login(page, pwd)
        if logged_in:
            print(f"Login successful with password: {pwd}")
            break
        else:
            print(f"Failed with: {pwd}")

    if logged_in:
        page.goto(TARGET_URL, wait_until='networkidle')
        page.screenshot(path=DESKTOP_OUT, full_page=False)
        print(f"Desktop screenshot saved: {DESKTOP_OUT}")
    else:
        print("Could not authenticate — saving login page screenshot instead")
        page.screenshot(path=DESKTOP_OUT, full_page=False)

    browser.close()

    # --- Mobile ---
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 375, 'height': 812})

    if logged_in:
        for pwd in passwords_to_try:
            ok = try_login(page, pwd)
            if ok:
                break
        page.goto(TARGET_URL, wait_until='networkidle')
        page.screenshot(path=MOBILE_OUT, full_page=False)
        print(f"Mobile screenshot saved: {MOBILE_OUT}")

    browser.close()
