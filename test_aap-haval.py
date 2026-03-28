from scraper_core import run_parse

if __name__ == "__main__":
    url = "https://aap-haval.ru"
    result = run_parse(
        url,
        force_llm=True,
        auto_detect_only=False,
        save_json=True,
        use_js=True,
    )
    print("used_llm:", result["used_llm"])
    print("niche:", result["normalized"].get("niche"))
    print("keys:", result["normalized"].keys())