# Varys

Personal planning and AI agent application — backend on VPS, Flutter client for Android, Linux, and macOS.

## Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI, SQLAlchemy 2, Alembic, SQLite (WAL) |
| Frontend | Flutter, Riverpod, GoRouter |
| AI | NVIDIA (extensible via `LLMProvider` interface) |
| Notifications | Self-hosted ntfy |

## Structure

```
varys/
├── varys-api/   # FastAPI application
├── varys-app/   # Flutter application
├── docs/        # Documentation
└── CLAUDE.md    # AI assistant instructions
```

## Rules

- AI API keys **never** leave the backend
- UI language: Turkish
- Code language: English
- Theme: Dark, minimalist

## Backend Quick Start

```bash
cd varys-api

# Install dependencies
uv sync

# Copy environment file
cp .env.example .env

# Run development server
uv run uvicorn app.main:app --reload
```

## Backend Testing

```bash
cd varys-api

# Run tests
uv run pytest
```

## Development

See `CLAUDE.md` for the operational protocol.