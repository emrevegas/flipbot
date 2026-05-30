# Flipbot Licensing System

Lisanslı dağıtım altyapısı: ana VDS’te API, müşteride kurulum + çalıştırma, Ada bot’ta yönetim.

## Mimari

```
┌─────────────────┐     HTTPS      ┌──────────────────────┐
│  Ada bot (sen)  │ ──────────────►│ License API (VDS)    │
│  /build         │                │ SQLite + IP whitelist│
│  /vds_manage    │                └──────────┬───────────┘
└────────┬────────┘                           │
         │ build_release.py                   │ validate / heartbeat
         ▼                                    ▼
┌─────────────────┐                ┌──────────────────────┐
│ GitHub Releases │ ◄── download ──│ Müşteri VDS          │
│ (Cython .so)    │                │ install.py → run.py  │
└─────────────────┘                └──────────────────────┘
```

## 1. License sunucusunu başlat (ana VDS — domain gerekmez)

Ana VDS `.env`:
```
LICENSE_SERVER_IP=123.45.67.89
LICENSE_SERVER_PORT=8787
LICENSE_ADMIN_KEY=...
LICENSE_ADMIN_IPS=123.45.67.89
```

```bash
pip install -r licensing/license_server/requirements.txt
uvicorn licensing.license_server.app:APP --host 0.0.0.0 --port 8787
```

Firewall'da **8787** portunu aç. Müşterilere verilecek adres:
`http://SENIN_VDS_IP:8787`

Test: `curl http://SENIN_VDS_IP:8787/health`

## 2. Ada bot komutları

Ada bot `.env`:
```
LICENSE_SERVER_IP=123.45.67.89
LICENSE_SERVER_PORT=8787
LICENSE_ADMIN_KEY=...
```

Extension yükle:
```python
await bot.load_extension("licensing.ada_bot.vds_panel")
```

| Komut | Açıklama |
|-------|----------|
| `/build 1.0.1` | Ubuntu Cython derlemesi + GitHub release + sunucuya kayıt |
| `/vds_manage create_license` | Günlük/haftalık/aylık lisans üret |
| `/vds_manage list_licenses` | Lisansları listele |
| `/vds_manage list_instances` | Çalışan botları gör |
| `/vds_manage suspend` / `revoke` / `extend` | Yönetim |

## 3. Müşteriye verilecek paket

```bash
python licensing/build/create_installer_zip.py
# → dist/flipbot-installer.zip
```

Müşteri:
```bash
unzip flipbot-installer.zip
pip install -r requirements.txt
python licensing/client/install.py
```

Kurulum sırası:
1. **Lisans anahtarı** → IP whitelist (süre boyunca bu IP)
2. Bot token, crypto mnemonic, treasury mnemonic
3. guild_id, owner_id, super_admin_id
4. Derlenmiş paket GitHub’dan indirilir → `runtime/`

Çalıştırma:
```bash
python licensing/client/run.py
# veya
bash licensing/client/start.sh
```

Her başlatmada: IP doğrulama → sürüm kontrolü → heartbeat → bot.

## 4. Build pipeline

```bash
python licensing/build/build_release.py --version 1.0.1
```

- `modules/`, `cogs/`, `Games/`, `database/` Cython ile `.so` derlenir
- `dist/flipbot-{version}-linux-x86_64.tar.gz` oluşur
- GitHub Releases’a yüklenir (token varsa)
- License API’ye `download_url` + sha256 kaydedilir

Test (Cython olmadan):
```bash
python licensing/build/build_release.py --version 1.0.0 --skip-cython
```

## 5. Releases repo

Ayrı public/private repo: `flipbot-releases` — sadece derlenmiş tarball’lar.
Şablon: `licensing/releases_repo/README.md`

## Güvenlik notları

- Mnemonic’ler müşteri `.env`’inde kalır; kaynak kodda tutulmaz.
- Lisans IP + machine_id ile bağlanır; başka makinede çalışmaz.
- **`run.py` bypass koruması değildir** — sadece kolay başlatıcıdır.
- Asıl kontrol **`modules/license_guard`** + **`modules/flipbot_launcher`** içinde; release build’inde Cython `.so` olarak derlenir.
- Müşteri paketinde `bot.py` yalnızca 3 satırlık stub’dır; bot mantığı `flipbot_core.so` içindedir.
- `python bot.py` veya `run.py` — ikisi de aynı compiled guard’dan geçer. Doğrudan `flipbot_core` import etmeye çalışmak `.so` olmadan mümkün değildir.
- Cython tam DRM değildir; kritik iş kurallarını mümkünse sunucu tarafında tut.

## API özeti

| Endpoint | Açıklama |
|----------|----------|
| `POST /api/v1/license/activate` | İlk kurulum, IP whitelist |
| `POST /api/v1/license/validate` | Çalıştırma öncesi kontrol |
| `POST /api/v1/instance/heartbeat` | Canlı instance bilgisi |
| `GET /api/v1/releases/latest` | Son sürüm indirme URL |
| `POST /api/v1/admin/licenses` | Lisans oluştur (X-Admin-Key) |
