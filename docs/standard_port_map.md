# Standard Port Map

- Unified backend (`sanmitra-backend`): `8000`
- LegalMitra frontend: `3000`
- GruhaMitra frontend: `3100`
- MandirMitra frontend: `3200`
- MitraBooks frontend: `3300`
- InvestMitra frontend: `3400`

## App Entry URLs

- LegalMitra: `http://localhost:3000/`
- GruhaMitra: `http://localhost:3100/login`
- MandirMitra: `http://localhost:3200/login`
- MitraBooks: `http://localhost:3300/login`
- InvestMitra: `http://localhost:3400/#/auth`

## Notes

- Backends should target unified API base `http://localhost:8000` for local dev unless explicitly overridden by env vars.
- Frontend env override keys in use:
  - CRA/CRACO apps: `PORT`, `REACT_APP_API_URL`
  - Vite apps: `VITE_API_URL`, Vite server `port` in `vite.config.ts`
