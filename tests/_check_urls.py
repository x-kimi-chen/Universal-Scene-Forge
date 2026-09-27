import requests

# 用 GET + stream 只取首块, 避免整包下载
URLS = [
    ("华为云pip-根页面", "https://repo.huaweicloud.com/repository/pypi/simple/"),
    ("华为云pip-具体包", "https://repo.huaweicloud.com/repository/pypi/simple/plyfile/"),
    ("Blender官方-HEAD403复测", "https://download.blender.org/release/Blender4.1/blender-4.1.1-windows-x64.zip"),
]
for name, url in URLS:
    try:
        with requests.get(url, stream=True, timeout=25, allow_redirects=True) as r:
            chunk = next(r.iter_content(1024), b"")
            print(f"{name:22s} {r.status_code}  first-bytes={len(chunk)}  ct={r.headers.get('Content-Type', '?')[:40]}")
    except Exception as e:
        print(f"{name:22s} FAIL {type(e).__name__}: {str(e)[:70]}")
