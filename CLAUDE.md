# Varys - Personal Planning & AI Agent Application

## Project Definition
Single-user application with a backend running on a VPS and a Flutter client (prioritizing Android, Linux, and macOS).

- **Backend Folder** : varys/varys-api
- **Frontend Folder** : varys/varys-app

## Technical Decisions
- **Backend**: FastAPI + SQLAlchemy 2 + Alembic + SQLite (WAL mode)
- **Frontend**: Flutter + Riverpod + GoRouter + feature-based modular architecture
- **AI Provider**: NVIDIA (extensible via an `LLMProvider` interface)
- **Notifications**: Self-hosted `ntfy` instance

## Strict Rules
1. **AI API keys must NEVER be stored on the client** — all AI interactions go through the backend
2. **User interface is in Turkish** — code and variable names must be in English
3. **Design is minimalist** with a focus on a dark theme

## Authentication
- **Single-user application** — no multi-user support
- **Password hashing**: Argon2 (argon2id) — only on the backend, never on the client
- **Client stores only JWT** in `flutter_secure_storage` (Keychain/Keystore)
- **Password is NEVER stored on the client** — not in memory after login, not in secure storage
- **No on-device lock system** — authentication is purely server-based
- **All endpoints require JWT** (Authorization: Bearer <token>) EXCEPT:
  - `GET /health` — public health check
  - `POST /auth/login` — password-based login, returns JWT
- **Token expiration**: 30 days (configurable via `JWT_EXPIRE_DAYS`)
- **Rate limiting**: 5 failed attempts per IP per 15 minutes → 429 Too Many Requests
- **On 401 response**: Client clears JWT and redirects to `/login`

## Authentication (Detailed)
- **Single-user setup**: The password (as an Argon2 hash) exists only on the server side (`PASSWORD_HASH` in backend `.env`).
- `POST /auth/login` returns a JWT; the client stores only the JWT (using `flutter_secure_storage` / Keychain/Keystore). The password is not stored on the client.
- There is no in-app lock mechanism.
- All endpoints, except for `/health` and `/auth/login`, require a JWT.
- Error mapping is based on the `DioException` type and the status code; substrings within the error message are not used for matching.
- **macOS**: Keychain configuration is done via `Release.entitlements` (adding `com.apple.security.keychain-read-write` and `network.client`).
- **On Linux**: `libsecret-1-dev` is required for `flutter_secure_storage` to work.
- New `.env` files must never be committed; only `.env.example` should be committed.

## Operational Protocol
- Proceed in small, incremental steps
- Before writing any code, briefly explain what you intend to do and why
- Modify no more than 5–6 files per step
- Do not add any features that were not requested
- At the end of each step, provide instructions on how to test the changes

## Github
- **Production branch**: main

## Github Remote Repos
- **Backend** (varys-api): https://github.com/afcelik/varys_api.git
- **Frontend** (varys-app): https://github.com/afcelik/varys.git

## Git Repository Structure
This project uses **two separate Git repositories**:
- **Root folder** (`/Users/afcelik/Projects/varys/`) 
- **`varys-app/` subfolder** → Frontend repository (connected to `varys.git`, initialized separately)
- **`varys-api/` subfolder** → Backend repository (connected to `varys_api.git`, initialized separately)

Each repository has its own `.git` directory and remote. **To push to a remote, change to the corresponding directory first:**
```bash
# Push backend
cd /Users/afcelik/Projects/varys/varys-api && git add . && git commit -m "[Write the changes here]" && git push origin main

# Push frontend
cd /Users/afcelik/Projects/varys/varys-app && git add . && git commit -m "[Write the changes here]" && git push origin main
```

## Navigation Rule
- The menu (bottom bar and side rail on wide screens) contains **only 3 tabs**: Home, Planning, and Varys. These remain within the `StatefulShellRoute`.
- All other pages (Notes, Inbox, Settings, and future pages) are **full-screen pages located OUTSIDE the shell** (e.g., `/notes`, `/inbox`, `/settings`).
- The menu is **not visible** on these full-screen pages.
- Navigation to these pages from the Home screen uses `context.push()` (not `go()`), ensuring the system back button or back arrow returns the user to the previous page.
- Every full-screen page must include a back arrow (`arrow_back`) in the top-left of the AppBar:
  - If `context.canPop()` is true, it performs a `pop()`.
  - Otherwise, it executes `go('/home')` (e.g., when the page is opened directly from a notification).
  - This logic is centralized in a shared widget (`BackArrow` in `shared/widgets/back_arrow.dart`).
- The login screen (`/login`) remains outside the shell.
- **Adding a new feature** implies creating a new full-screen page plus a corresponding widget on the Home Screen; the menu (the 3 tabs) remains unchanged.

## Progress
- **Step 13: Simplified Navigation** — Reduced shell tabs to 3 (Home, Planning, Varys). Moved Notes, Inbox, Settings to full-screen pages outside shell with push navigation and shared back arrow widget.