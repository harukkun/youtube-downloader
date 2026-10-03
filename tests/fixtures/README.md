`thumbnail.heic` is a synthetic 108 × 192 color gradient, generated from the
`/_test/image` PNG in `tests/upload_browser_fixture.py` using macOS:

```sh
curl http://127.0.0.1:8877/_test/image -o /tmp/thumbnail.png
sips -s format heic /tmp/thumbnail.png --out tests/fixtures/thumbnail.heic
```

It contains no user photos. Run the HEIC browser checks with the fixture server
running and Playwright available: `node tests/test_thumbnail_heic.js`.
