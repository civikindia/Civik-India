import os
import re
import urllib.request
import hashlib

def main():
    print("Starting font downloader...")
    os.makedirs('app/static/vendor/fonts', exist_ok=True)
    os.makedirs('app/static/css', exist_ok=True)

    url = "https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700;800&family=Noto+Sans+Devanagari:wght@400;500;600;700;800&display=swap"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            css_content = response.read().decode('utf-8')
    except Exception as e:
        print(f"Error fetching CSS: {e}")
        return

    # Find all urls
    urls = re.findall(r'url\((https://[^)]+)\)', css_content)
    unique_urls = list(set(urls))
    print(f"Found {len(unique_urls)} unique font files to download.")

    url_to_local = {}
    for i, font_url in enumerate(unique_urls):
        # Generate a stable clean local filename
        # e.g., get the original name or hash it
        parsed_name = font_url.split('/')[-1]
        local_filename = f"font_{i}_{parsed_name}"
        local_path = os.path.join('app/static/vendor/fonts', local_filename)

        print(f"Downloading {font_url} -> {local_path} ...")
        try:
            req_font = urllib.request.Request(font_url, headers=headers)
            with urllib.request.urlopen(req_font) as res_font:
                with open(local_path, 'wb') as f:
                    f.write(res_font.read())
            url_to_local[font_url] = f"../vendor/fonts/{local_filename}"
        except Exception as e:
            print(f"Failed to download {font_url}: {e}")

    # Now rewrite css content to point to local files
    local_css = css_content
    for remote_url, local_rel_path in url_to_local.items():
        local_css = local_css.replace(remote_url, local_rel_path)

    with open('app/static/css/fonts.css', 'w', encoding='utf-8') as f:
        f.write(local_css)

    print("Successfully generated app/static/css/fonts.css pointing to local assets.")

if __name__ == "__main__":
    main()
