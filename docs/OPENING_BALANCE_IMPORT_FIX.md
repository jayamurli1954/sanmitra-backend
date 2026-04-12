# MandirMitra Opening Balance Import Fix

## Issue
The opening balance import endpoint for MandirMitra was not providing clear success/failure messages to the frontend, making it difficult for users to understand whether their file upload was successful or failed.

## Solution
Updated the `/opening-balances/import` endpoint to include:
1. **Clear success/failure status** - A boolean `success` field and `status` field
2. **Meaningful messages** - User-friendly messages with emoji indicators
3. **Detailed response data** - Counts for processed, updated, skipped, and error rows
4. **Error handling** - Proper exception handling for unexpected errors

## Response Format

### Success Response
```json
{
  "success": true,
  "status": "success",
  "message": "✓ Successfully imported 5 opening balance(s)",
  "processed_count": 5,
  "updated_count": 5,
  "skipped_count": 0,
  "error_count": 0,
  "updated": [
    {
      "account_code": "11001",
      "account_name": "Cash",
      "opening_balance_debit": 10000,
      "opening_balance_credit": 0,
      "applied_delta": 10000
    }
  ],
  "errors": []
}
```

### Partial Success Response
```json
{
  "success": false,
  "status": "partial",
  "message": "⚠ Partial success: 3 imported, 2 failed",
  "processed_count": 5,
  "updated_count": 3,
  "skipped_count": 0,
  "error_count": 2,
  "updated": [...],
  "errors": [
    {
      "row": 4,
      "error": "Account 'INVALID' not found"
    }
  ]
}
```

### Failure Response
```json
{
  "success": false,
  "status": "failed",
  "message": "✗ Import failed: All 5 row(s) had errors",
  "processed_count": 5,
  "updated_count": 0,
  "skipped_count": 0,
  "error_count": 5,
  "updated": [],
  "errors": [...]
}
```

## Response Fields

| Field | Type | Description |
|-------|------|-------------|
| `success` | boolean | True if all rows were processed successfully without errors |
| `status` | string | One of: `success`, `partial`, `failed` |
| `message` | string | Human-readable message with emoji indicator |
| `processed_count` | integer | Total rows from the file |
| `updated_count` | integer | Rows successfully imported |
| `skipped_count` | integer | Rows that were skipped (no changes needed) |
| `error_count` | integer | Rows that failed during import |
| `updated` | array | List of successfully imported opening balances |
| `errors` | array | List of errors (max 200 items) |

## Status Codes

- **success** (boolean true) - All rows imported without errors
- **partial** - Some rows imported, some failed
- **failed** - No rows were imported, all had errors

## Frontend Implementation

### Success Handling
```javascript
if (response.success) {
  showSuccessMessage(response.message);
  refreshAccountList();
} else if (response.status === "partial") {
  showWarningMessage(response.message);
  showErrors(response.errors);
  refreshAccountList();
} else {
  showErrorMessage(response.message);
  showErrors(response.errors);
}
```

## File Format Support

The endpoint supports:
- **XLSX files** (.xlsx, .xlsm)
- **CSV files** (.csv)

### Required Columns
At least one of:
- `account_code` - The account code (e.g., "11001")
- `legacy_code` - Legacy account code
- `code` - Alternative account code format

### Opening Balance Columns
At least one of:
- `opening_balance` - Signed value (positive for debit, negative for credit)
- `opening_balance_debit` - Debit amount
- `opening_balance_credit` - Credit amount

### Optional Columns
- `account_name` - Account name (helps with account matching)
- `name` - Alternative account name field

## Example CSV

```csv
account_code,account_name,opening_balance
11001,Cash,10000
11002,Bank Account,50000
21001,Loan Payable,-30000
```

## Example XLSX

Create a spreadsheet with columns:
- account_code
- account_name
- opening_balance

Then upload the file.

## Error Handling

Common errors:
- **"File name is required"** - Upload file has no filename
- **"Uploaded file is empty"** - File has no content
- **"Import file is empty"** - File has no data rows
- **"Unsupported file format"** - Use .csv or .xlsx format
- **"Account 'XXX' not found"** - Account code doesn't exist
- **"Only balance sheet accounts can have opening balances"** - Income/Expense accounts aren't allowed

## Testing

Run the test suite:
```bash
python -m pytest tests/test_mandir_opening_balance_import.py -v
```

## Changes Made

**File**: `app/modules/mandir_compat/router.py`

1. Updated the `/opening-balances/import` endpoint (line 4567)
2. Added `success` and `status` fields to response
3. Improved message formatting with emoji indicators
4. Added `processed_count`, `error_count` fields
5. Wrapped entire endpoint in try-catch for unexpected errors
6. Maintains backward compatibility with existing `updated`, `errors`, `updated_count`, `skipped_count` fields

## Backward Compatibility

The updated endpoint maintains backward compatibility:
- All existing response fields are preserved
- New fields are additions only
- Frontend can check the new `success` field for clear status indication
- Existing clients can continue using the old field structure if needed

## Testing the Fix

1. Create a test XLSX file with account codes and opening balances
2. Upload via POST to `/opening-balances/import`
3. Check the `success` field to determine if upload was successful
4. Display the `message` to the user
5. If there are errors, show them from the `errors` array
