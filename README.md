# 📊 Sales Telegram Bot

Kirim pesan penjualan melalui Telegram, periksa hasil parsing, lalu simpan ke
Google Sheets. Bot memakai Claude untuk mengubah teks bebas menjadi data
terstruktur dan berjalan sebagai webhook di Google Cloud Run.

## Arsitektur

```text
Telegram Bot API
       │ HTTPS webhook
       ▼
Google Cloud Run (scale to zero)
       ├── Claude API — parsing teks
       └── Google Sheets API — penyimpanan data
```

Cloud Run tidak perlu terus berjalan saat bot tidak digunakan. Untuk penggunaan
ringan, workload ini biasanya tetap berada dalam free tier Cloud Run. Biaya API
Claude tetap terpisah.

## Fitur

- Parsing pesan penjualan berbahasa Indonesia atau Inggris
- Preview dan tombol konfirmasi sebelum data disimpan
- Pencatatan tanggal dalam zona waktu WIB
- Pemilihan tab spreadsheet melalui `/settab`
- Konfigurasi tab aktif tersimpan di worksheet tersembunyi `_BotConfig`
- Endpoint webhook dilindungi secret header Telegram

## 1. Buat Telegram Bot

1. Buka Telegram dan cari **@BotFather**.
2. Kirim `/newbot` dan ikuti instruksinya.
3. Simpan bot token yang diberikan.

## 2. Siapkan Google Sheets

1. Buat Google Sheet baru dan salin spreadsheet ID dari URL-nya.
2. Di Google Cloud Console, aktifkan Google Sheets API dan Google Drive API.
3. Buat service account dan unduh key berformat JSON.
4. Share spreadsheet ke `client_email` service account dengan akses Editor.

Bot akan membuat tab `Sales` dan worksheet tersembunyi `_BotConfig` jika belum
tersedia.

## 3. Konfigurasi

Environment variables yang digunakan:

| Variable | Wajib | Keterangan |
|---|---:|---|
| `TELEGRAM_TOKEN` | Ya | Token dari BotFather |
| `ANTHROPIC_API_KEY` | Ya | API key Anthropic |
| `SPREADSHEET_ID` | Ya | ID Google Spreadsheet |
| `GOOGLE_CREDENTIALS_JSON` | Ya | Seluruh isi JSON service account |
| `TELEGRAM_WEBHOOK_SECRET` | Ya | String acak untuk memverifikasi request Telegram |
| `WEBHOOK_BASE_URL` | Setelah deploy | URL service Cloud Run, tanpa trailing slash |
| `WEBHOOK_PATH` | Tidak | Path webhook; default `telegram` |
| `PORT` | Otomatis | Diisi oleh Cloud Run; default lokal `8080` |

Contoh nama variable tersedia di `.env.example`. Jangan commit nilai rahasia.

Untuk membuat webhook secret:

```bash
openssl rand -hex 32
```

## 4. Deploy ke Google Cloud Run

Deploy pertama belum memerlukan `WEBHOOK_BASE_URL`. Aplikasi akan melayani HTTP,
tetapi belum mendaftarkan webhook sampai URL tersebut ditambahkan.

1. Buat project Google Cloud dan aktifkan billing, Cloud Run API, Cloud Build API,
   dan Artifact Registry API.
2. Salin `.env.example` menjadi `.env`, isi semua variable wajib, dan biarkan
   `WEBHOOK_BASE_URL` kosong untuk deploy pertama.
3. Dari folder repository, deploy source:

   ```bash
   gcloud run deploy sales-telegram-bot \
     --source . \
     --region asia-southeast2 \
     --allow-unauthenticated \
     --min 0 \
     --max 1 \
     --env-vars-file .env
   ```

4. Salin URL service yang ditampilkan, misalnya
   `https://sales-telegram-bot-xxxxx.asia-southeast2.run.app`, dan simpan sebagai
   `WEBHOOK_BASE_URL` di `.env`.
5. Jalankan perintah deploy yang sama sekali lagi. Saat revision baru startup,
   bot otomatis mendaftarkan URL webhook ke Telegram.

Untuk production, simpan token, API key, dan credentials JSON di Google Secret
Manager lalu hubungkan secret tersebut sebagai environment variables Cloud Run.

### Verifikasi deployment

Endpoint root harus mengembalikan `{"status":"ok"}`:

```bash
curl https://YOUR-SERVICE-URL.run.app/
```

Kemudian kirim pesan berikut ke bot:

```text
bakso ayam 10 porsi, es teh 20 gelas - Andi
```

## Penggunaan

| Perintah | Fungsi |
|---|---|
| `/start` | Sambutan dan daftar perintah |
| `/help` | Panduan penggunaan |
| `/settab <nama>` | Pilih tab aktif; tab dibuat ketika data pertama disimpan |
| `/currenttab` | Tampilkan tab aktif |
| `/listtabs` | Tampilkan semua tab data |

Contoh input:

```text
kaos polos 5 pcs sama celana jeans 3 - Andi, dari uniqlo
vitamin c 10, sabun muka 5 - Siti
beras 5kg, minyak goreng 3 botol - Budi
```

## Kolom Google Sheets

| Date | Name | Store | Item Name | Quantity |
|---|---|---|---|---:|
| 2026-05-24 14:30 | Andi | Supermarket | Bakso Ayam | 10 |

## Menjalankan secara lokal

Webhook lokal memerlukan URL HTTPS publik, misalnya melalui tunnel. Instal
dependency dan export variable dari `.env.example`, lalu jalankan:

```bash
python -m pip install -r requirements.txt
python bot.py
```

Set `WEBHOOK_BASE_URL` ke URL HTTPS tunnel. Health endpoint tersedia di port
`8080` secara default.

## Struktur

```text
.
├── bot.py
├── sheets.py
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .env.example
└── README.md
```

## Troubleshooting

- **Container gagal startup:** pastikan seluruh variable wajib kecuali
  `WEBHOOK_BASE_URL` tersedia dan JSON service account valid.
- **Bot tidak merespons:** pastikan `WEBHOOK_BASE_URL` sama dengan URL service dan
  revision terbaru berhasil dijalankan.
- **Google Sheets gagal:** pastikan spreadsheet sudah di-share ke email service
  account dengan akses Editor.
- **Webhook mendapat 403:** pastikan `TELEGRAM_WEBHOOK_SECRET` tidak diubah tanpa
  menjalankan revision baru agar webhook didaftarkan ulang.
