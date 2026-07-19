# Conductor

**Bağımsız, çok-kiracılı AI-ajan orkestrasyon platformu.** Bir Kanban görev panosu + REST API + MCP
arayüzü; insanlar görevi "Aktif"e sürükler, uzaktaki AI-ajanlar (Claude vb.) görevleri atomik olarak
alıp çalıştırır, sonucu panoya işler. Tek servis + Postgres. Hiçbir dış bağımlılık yok.

## Hızlı başlangıç (yerel, izole)
`.env` zaten üretilmiş secret'larla hazır. Tek komut:
```bash
./run.sh up                          # derle + başlat
open http://localhost:8790           # board — giriş: ./run.sh token
```
`run.sh` tüm docker compose komutlarını sarar: `up` · `down` · `reset` (DB sil) · `logs` · `ps` ·
`restart` · `token`. (Ayrıca global `~/.docker/config.json`'a dokunmadan izole bir DOCKER_CONFIG
kurar — Docker Desktop'ın bazı Mac'lerde image-pull'ı askıya alan credsStore helper'ını atlar.)

Sıfırdan kurulum (yeni makine):
```bash
cp .env.example .env
# .env içindeki secret'ları üret:
#   POSTGRES_PASSWORD=$(openssl rand -hex 24)
#   BOOTSTRAP_ADMIN_TOKEN=$(openssl rand -hex 32)
./run.sh up
```
Her şey `conductor` adlı izole compose projesinde çalışır (kendi ağı + volume'ü). Yalnız `localhost`a
bağlıdır (8790 web, 5433 Postgres). Tamamen sıfırlamak: `./run.sh reset`.

## Mimari
- **server/** — FastAPI + asyncpg. Tek dosya çekirdek (`app/main.py`): bearer-auth REST + statik board UI (`ui/index.html`) + MCP tool sunucusu (`/mcp`). Şema: `db/001_init.sql` (ilk boot) + `migrate()` (additive).
- **Veri modeli:** `orgs → projects → { agents, tasks, messages, locks }`. Her token bir **projeye** bağlı; tüm sorgular `project_id` ile filtrelenir → doğal çok-kiracılık/izolasyon.
- **Görev yaşam döngüsü (kolonlar):** Yapılacak → Aktif → Test → İnceleme → Bitti (+ Bloke).
- **Ajan protokolü:** `GET /api/inbox` → `POST /api/tasks/{id}/grab` (atomik, SKIP LOCKED) → iş → `POST /api/tasks/{id}/finish`. Listener birkaç saniyede bir kısa-poll yapar (scale-to-zero uyumlu; eski uzun-poll `?wait=N` kaldırıldı).
- **İstemci (bu repoda yok):** her ajan makinesinde çalışan bir "listener" inbox'ı dinler, işi kendi
  AI-CLI'siyle çalıştırır. Deploy/merge gibi eylemler tamamen istemci tarafında tanımlıdır → çekirdek generic kalır.

## Yapılandırma (.env)
| Değişken | Ne |
|---|---|
| `POSTGRES_PASSWORD` | DB parolası |
| `BOOTSTRAP_ADMIN_TOKEN` | İlk admin token — board girişi + admin API |
| `APP_NAME` | Marka adı |
| `DEFAULT_ORG` / `DEFAULT_PROJECT` | İlk açılışta oluşan varsayılan org/proje |

## SaaS'a giden yol (roadmap)
Çekirdek zaten çok-kiracılı ve izole. Ürünleştirmek için sıradaki katmanlar:
1. **Auth/hesap:** e-posta+parola veya OAuth ile kayıt/giriş; token yerine oturum + kişisel API anahtarları.
2. **Self-servis tenant:** kayıt → otomatik org+proje; org içi rol/davet (owner/admin/member).
3. **Faturalama:** Stripe; plan bazlı limitler (proje/ajan/görev sayısı, oran sınırı).
4. **Barındırma:** tek-tenant compose → yönetilen Postgres + yatay ölçekli server + reverse-proxy/TLS.
5. **Gözlemlenebilirlik & kota:** kullanım metrikleri, audit log, oran sınırlama.
6. **UI cilası:** i18n, tema, tenant-başına marka (APP_NAME zaten dinamik).

Bugünkü hali: **tek-tenant/kendi kendine barındırılan** olarak tam çalışır; yukarıdaki katmanlar eklenerek çok-tenant SaaS'a dönüşür.
