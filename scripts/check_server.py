import urllib.request
for path in ['/', '/settings/schema']:
    url = f'http://127.0.0.1:5000{path}'
    try:
        r = urllib.request.urlopen(url, timeout=5)
        body = r.read().decode('utf-8', errors='replace')
        print('URL', url)
        print('STATUS', r.getcode())
        print('HEADERS', dict(r.getheaders()))
        print(body[:400])
    except Exception as e:
        print('URL', url, 'ERROR', type(e).__name__, e)
