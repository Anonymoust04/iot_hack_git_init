# Aiven MySQL setup

We use one shared **MySQL 8** database hosted on [Aiven](https://aiven.io) (free plan).
One person creates it (steps 1–3, **already done**) and shares the connection details.
Everyone else starts at step 4.

What the tables mean and how to test each part: [`database/README.md`](../database/README.md).

> ⚠️ **The venue Wi-Fi (`EVENT-2026`) blocks MySQL connections.** The connection opens, then
> hangs for about 19 s and fails with `WinError 10060`. **Use a phone hotspot** on any machine that
> talks to the database.

---

## 1. Create an Aiven account (done)

Sign up at <https://console.aiven.io/signup>, then create an organization and a project. No credit card is needed.

## 2. Create the MySQL service (done)

**Create service** → **MySQL** → plan **Free** → nearest region → name `carpark-mysql` → wait for **Running**.

## 3. Database name (done)

We use the built-in database **`defaultdb`**.

---

## 4. Get the connection details

Aiven console → service → **Overview** → **Connection information**:

| Aiven shows | Put in `.env` as |
| --- | --- |
| Host | `DB_HOST` |
| Port | `DB_PORT` |
| Database name | `DB_NAME` (`defaultdb`) |
| User | `DB_USER` (`avnadmin`) |
| Password | `DB_PASSWORD` |
| CA certificate → *Download* | save as `certs/aiven-ca.pem` (already in the repo) |

- **Commit the CA certificate:** `certs/aiven-ca.pem` is public, so it's safe to commit.
- **Never commit the password:** `.env` is in `.gitignore`. Share the password privately (DM or password manager).

## 5. Configure your machine

```bash
copy .env.example .env      # macOS/Linux: cp .env.example .env
```

Then fill in `.env`:

```env
DB_HOST=<service>-<project>.k.aivencloud.com
DB_PORT=<port>
DB_NAME=defaultdb
DB_USER=avnadmin
DB_PASSWORD=<from Aiven>
DB_SSL_CA=certs/aiven-ca.pem
DB_POOL_SIZE=3
TEST_DB_NAME=carpark_test
```

The connection uses **TLS with full certificate verification**. The server certificate must be
signed by the Aiven CA and match the hostname. Verification is never switched off.

## 6. Create the tables and test the connection

```bash
cd backend
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt   # first time only
python -m app.db.init_db
```

Expected output:

```
Connecting to mysql+pymysql://avnadmin:***@<service>-<project>.k.aivencloud.com:<port>/defaultdb?charset=utf8mb4 ...
OK: MySQL 8.4.8, TLS: TLS_AES_256_GCM_SHA384
INFO Seeded admin user 'admin'
Tables: events, gates, parking_sessions, parking_spots, users
Done.
```

This runs [`database/schema.sql`](../database/schema.sql) on Aiven. You never need to paste the
SQL anywhere by hand. It's safe to run again, because existing tables are skipped.
`--reset` drops and recreates everything (**deletes all data**).

## 7. (Optional) Browse the data with a GUI

**MySQL Workbench**, **DBeaver**, or the VS Code "MySQL" extension. Use the host, port, user and password
from step 4, and database `defaultdb`. Set SSL to **Require** and point the CA file to `certs/aiven-ca.pem`.
Then open [`database/queries.sql`](../database/queries.sql) and run the queries.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Hangs about 19 s, then `WinError 10060` / `Can't connect` / `Lost connection` | **Network blocks MySQL** (venue Wi-Fi). Switch to a phone hotspot. If it still fails, check the service is **Running** in the Aiven console (idle free services can be powered off), then try disabling McAfee's firewall. |
| `Access denied for user 'avnadmin'` | Wrong `DB_PASSWORD`. Re-copy it from Aiven. Special characters are fine (no URL-encoding needed). |
| `certificate verify failed` | `certs/aiven-ca.pem` is from a different Aiven project, or corrupted. Download it again. |
| `DB_SSL_CA file not found` | Put the CA file at `certs/aiven-ca.pem`. |
| `Too many connections` | The free plan has a small limit shared by the whole team. Keep `DB_POOL_SIZE` at 2–3 and stop backends you're not using. |
| `Database is out of date with database/schema.sql: ... missing columns [...]` | `schema.sql` gained a column after your tables were created (`init_db` never alters existing tables). Add it with `ALTER TABLE` (keeps data), or `python -m app.db.init_db --reset` (**deletes all data**). |
| Tests all **skipped** | `pytest` couldn't reach MySQL (same network issue). Run `pytest -rs` to see why. |
