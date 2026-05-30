# Obfuscated Flipbot releases — populated by /build (build_release.py)
# Do not commit compiled .tar.gz here if using GitHub Releases instead.

## Release asset naming
- `flipbot-{version}-linux-x86_64.tar.gz`

## Manifest (auto-registered on license server)
```json
{
  "version": "1.0.1",
  "platform": "linux-x86_64",
  "download_url": "https://github.com/YOU/flipbot-releases/releases/download/v1.0.1/flipbot-1.0.1-linux-x86_64.tar.gz",
  "sha256": "..."
}
```

Create empty repo on GitHub, set `RELEASES_GITHUB_REPO=YOUR_USER/flipbot-releases` in Ada bot `.env`.
