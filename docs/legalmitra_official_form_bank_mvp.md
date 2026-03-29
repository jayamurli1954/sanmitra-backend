# LegalMitra Official Form Bank (MVP)

This MVP supports uploading official PDF forms and reusing them as indexed assets.

## Why this path

Many government forms (for example GST REG-01) are distributed as static PDFs without embedded AcroForm fields. This MVP stores those official forms, profiles them, and produces filled PDF outputs using:

1. Embedded-field fill (when form fields exist), or
2. Overlay + annexure fill (when no embedded fields exist).

## Required indexing metadata at upload

- `form_name` (required)
- `purpose` (required)
- `department` (required)
- `form_code` (optional)
- `description` (optional)

## API endpoints

Base prefix: `/api/v1`

1. `POST /v2/official-forms/upload`
- Multipart form-data
- File key: `file`
- Metadata keys: `form_name`, `purpose`, `department`, optional `form_code`, `description`
- Headers: `X-Tenant-ID` (optional), `X-App-Key` (optional)

2. `GET /v2/official-forms`
- Optional query: `department`, `search`, `limit`

3. `GET /v2/official-forms/{form_id}`
- Fetch one form profile

4. `POST /v2/official-forms/{form_id}/render-pdf`
- JSON body: `{ "fields": { ... } }`
- Returns downloadable PDF

## Example request flow

1. Upload official GST REG-01 PDF with metadata:
- `form_name`: `GST REG-01 Application for Registration`
- `purpose`: `GST new registration filing`
- `department`: `GST`
- `form_code`: `REG-01`

2. Call list endpoint and pick `form_id`.

3. Submit key-value data to render endpoint and download the filled output PDF.

## Current limitation

For static non-fillable PDFs, precise per-box placement may still need tuning per form layout. The annexure page ensures all submitted data remains part of the generated output in this MVP.
