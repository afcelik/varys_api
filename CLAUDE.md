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
- **Root folder** (`/Users/afcelik/Projects/varys/`) → Backend repository (connected to `varys_api.git`)
- **`varys-app/` subfolder** → Frontend repository (connected to `varys.git`, initialized separately)

Each repository has its own `.git` directory and remote. **To push to a remote, change to the corresponding directory first:**
```bash
# Push backend
cd /Users/afcelik/Projects/varys && git push origin main

# Push frontend
cd /Users/afcelik/Projects/varys/varys-app && git push origin main
```