# Official Form Upload (MVP) - Compatibility and Validation

This defines what users must upload, and what errors they should see when upload is not compatible.

## Supported Upload Type
- File type: `.pdf` only
- MIME type: `application/pdf`
- Recommended: official authority/government PDF with selectable text (not image-only scans)

## Required Metadata
These are mandatory for indexing and monetization-ready cataloging:
- `form_name` (example: `GST REG-01 Application for Registration`)
- `purpose` (example: `GST registration application filing`)
- `department` (example: `GST`, `Income Tax`, `MCA`, `Labour`)

Optional metadata:
- `form_code`
- `description`

## Size and Compatibility Limits
- Max upload size: controlled by `LEGAL_OFFICIAL_FORM_MAX_UPLOAD_MB` (default `20` MB)
- Max pages: controlled by `LEGAL_OFFICIAL_FORM_MAX_PAGES` (default `80` pages)
- Non-fillable PDFs must have machine-readable labels:
  - minimum extracted labels controlled by `LEGAL_OFFICIAL_FORM_MIN_SUGGESTED_LABELS` (default `3`)

## API Endpoints
- Guidelines: `GET /api/v1/v2/official-forms/upload-guidelines`
- Upload: `POST /api/v1/v2/official-forms/upload`

## Validation Error Shape
Upload returns HTTP `400` with structured detail:

```json
{
  "detail": {
    "code": "upload_too_large",
    "message": "Uploaded file exceeds 20 MB",
    "hint": "Compress/split the form and keep PDF size <= 20 MB"
  }
}
```

## Validation Error Codes
- `missing_metadata`
- `upload_too_large`
- `unsupported_file_type`
- `incompatible_pdf`

## Recommended Frontend UX Copy
- Before upload:
  - "Upload official PDF form (max 20 MB)."
  - "Required: Form Name, Purpose, Department."
  - "Best results: unlocked PDF with selectable text."
- On error:
  - Show `detail.message` as primary error.
  - Show `detail.hint` as secondary helper text.
  - Do not show generic `Upload failed` unless no detail is available.
