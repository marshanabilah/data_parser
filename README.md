# 📊 Sales Telegram Bot

Kirim pesan penjualan via Telegram → otomatis masuk Google Sheets. No manual input!

---

## 🏗️ Arsitektur

```
Kamu (Telegram)
      ↓
  Telegram Bot API (gratis)
      ↓
  Python di Railway (gratis)
  ├── python-telegram-bot
  ├── Claude API (parse teks → JSON)
  └── gspread (tulis ke Google Sheets)
      ↓
  Google Sheets
```

---

## 🛠️ Setup (ikuti urutan ini!)

### Step 1 — Buat Telegram Bot

1. Buka Telegram, cari **@BotFather**
2. Kirim `/newbot`
3. Ikuti instruksi, pilih nama dan username bot
4. Salin **Bot Token** yang diberikan (format: `123456:ABC-DEF...`)

---

### Step 2 — Siapkan Google Sheets & Service Account

#### 2a. Buat Google Sheet
1. Buka [sheets.google.com](https://sheets.google.com)
2. Buat spreadsheet baru
3. Salin **Spreadsheet ID** dari URL:
   ```
   https://docs.google.com/spreadsheets/d/SPREADSHEET_ID_ADA_DI_SINI/edit
   ```

#### 2b. Buat Service Account (Google)
1. Buka [console.cloud.google.com](https://console.cloud.google.com)
2. Buat project baru (atau pakai yang sudah ada)
3. Aktifkan dua API ini:
   - **Google Sheets API**
   - **Google Drive API**
4. Buka **IAM & Admin → Service Accounts**
5. Klik **Create Service Account**
6. Isi nama, klik **Create and Continue**, lalu **Done**
7. Klik service account yang baru dibuat
8. Tab **Keys → Add Key → Create new key → JSON**
9. File JSON akan ter-download otomatis — **simpan baik-baik!**

#### 2c. Share Sheet ke Service Account
1. Buka file JSON tadi, cari field `"client_email"` (contoh: `mybot@myproject.iam.gserviceaccount.com`)
2. Buka Google Sheet kamu
3. Klik **Share**, paste email service account tadi
4. Beri akses **Editor**, klik **Send**

---

### Step 3 — Deploy ke Railway

1. Buka [railway.app](https://railway.app) dan login (bisa pakai GitHub)
2. Klik **New Project → Deploy from GitHub repo**
   - Atau drag & drop folder project ini
3. Setelah project terbuat, buka tab **Variables**
4. Tambahkan variabel berikut (klik **+ New Variable** untuk tiap variabel):

| Variable Name | Value |
|---|---|
| `TELEGRAM_TOKEN` | Token dari BotFather |
| `ANTHROPIC_API_KEY` | API key dari [console.anthropic.com](https://console.anthropic.com) |
| `SPREADSHEET_ID` | ID Google Sheet kamu |
| `GOOGLE_CREDENTIALS_JSON` | **Isi konten file JSON** service account (copy-paste semua isinya) |
| `SHEET_NAME` | `Sales` (atau nama tab sheet kamu) |

5. Railway akan otomatis deploy. Tunggu hingga status **Active** ✅

---

### Step 4 — Test Bot

Buka bot kamu di Telegram, kirim:
```
bakso ayam 10 porsi, es teh 20 gelas - Andi
```

Bot akan balas konfirmasi dan data masuk ke sheet! 🎉

---

## 💬 Format Pesan

Bot sangat fleksibel, bisa terima berbagai format:

| Pesan | Yang Dicatat |
|---|---|
| `bakso 10 - Budi` | Bakso, qty 10, Budi |
| `jual nasi goreng 15 porsi sama mie ayam 8` | 2 baris, tanpa nama |
| `es teh 20 gelas, jus alpukat 8 gelas - Siti` | 2 baris, nama Siti |
| `total penjualan: ayam bakar 5, lele 12 - Andi` | 2 baris, nama Andi |

---

## 📋 Kolom di Google Sheets

| Date | Item Name | Quantity | Name |
|---|---|---|---|
| 2026-05-24 14:30 | Bakso Ayam | 10 | Andi |
| 2026-05-24 14:30 | Es Teh | 20 | Andi |

---

## 🔧 Troubleshooting

**Bot tidak merespons:**
- Cek log di Railway dashboard (tab **Deployments → View Logs**)
- Pastikan `TELEGRAM_TOKEN` benar

**Error Google Sheets:**
- Pastikan `GOOGLE_CREDENTIALS_JSON` berisi konten JSON lengkap (bukan path file)
- Pastikan sheet sudah di-share ke email service account dengan akses Editor

**Data tidak terparsing:**
- Coba format lebih jelas: `[nama item] [jumlah] - [nama]`
- Bot bisa bahasa Indonesia dan Inggris

---

## 📁 Struktur File

```
sales-bot/
├── bot.py              # Telegram bot + Claude parsing
├── sheets.py           # Google Sheets integration
├── requirements.txt    # Python dependencies
├── railway.toml        # Railway deployment config
└── README.md           # Panduan ini
```

---

## 🔑 Mendapatkan Anthropic API Key

1. Buka [console.anthropic.com](https://console.anthropic.com)
2. Login / daftar akun
3. Buka **API Keys → Create Key**
4. Salin key dan masukkan ke Railway variable `ANTHROPIC_API_KEY`
