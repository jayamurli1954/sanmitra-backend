# GruhaMitra Tenant Isolation Security Review
**Status:** CRITICAL VULNERABILITIES IDENTIFIED
**Date:** 2026-04-30
**Scope:** GruhaMitra (housing) module with strict app_key + tenant_id isolation
**Reviewer:** Security Analysis (Pre-Launch Testing)

---

## Executive Summary

A comprehensive security review of the GruhaMitra module has identified **3 CRITICAL vulnerabilities** that violate strict multi-tenancy data isolation requirements. All vulnerabilities must be patched before production launch to prevent cross-tenant data leakage.

**Risk Level:** 🔴 **CRITICAL**
**Data Impact:** Tenant/app_key boundaries can be violated
**Affected Collections:** housing_maintenance_collections, users (gruhamitra routes)
**Action Required:** Immediate remediation before launch

---

## Vulnerabilities Discovered

### 🔴 CRITICAL-1: Missing app_key in housing_maintenance_collections

**Location:** `app/modules/housing/service.py:21-84`
**Severity:** CRITICAL
**Type:** Data Isolation Bypass

#### The Issue
When a maintenance collection is recorded, the MongoDB document is inserted **without the app_key field**:

```python
# app/modules/housing/service.py:31-42 (VULNERABLE)
doc = {
    "collection_id": collection_id,
    "tenant_id": tenant_id,
    # ❌ MISSING: "app_key": app_key,
    "amount": float(payload.amount),
    "flat_number": payload.flat_number,
    "resident_name": payload.resident_name,
    # ... rest of fields
}
await collections.insert_one(doc)
```

**Indexes are also missing app_key enforcement:**
```python
# app/modules/housing/service.py:15-18 (VULNERABLE)
await collections.create_index([("tenant_id", 1), ("collected_on", -1)])
await collections.create_index("collection_id", unique=True)
# ❌ Missing: [("tenant_id", 1), ("app_key", 1), ...]
```

#### Impact
- **Isolation Bypass:** A GruhaMitra user can theoretically query across tenant_id + app_key boundaries
- **Query Risk:** If queries use only `tenant_id` without app_key, all maintenance collections for a tenant are visible
- **Compliance Violation:** Violates AGENTS.md requirement: "never allow cross-tenant data leakage"

#### Proof of Concept (Vulnerability)
```python
# An admin with tenant_id="T123" could query:
query = {"tenant_id": "T123"}  # Missing app_key filter
# Returns ALL maintenance collections for T123 across ALL app_keys
```

---

### 🔴 CRITICAL-2: Conditional app_key Enforcement in /users/ Endpoint

**Location:** `app/api/gruhamitra_compat_router.py:92-115`
**Severity:** CRITICAL
**Type:** Authentication/Authorization Bypass

#### The Issue
The `/users/` endpoint conditionally enforces app_key only if it exists in the user's token:

```python
# app/api/gruhamitra_compat_router.py:92-115 (VULNERABLE)
@router.get("/users/")
async def users_list_compat_endpoint(current_user: dict = Depends(get_current_user)):
    _require_role_admin(current_user)
    tenant_id = str(current_user.get("tenant_id") or "").strip()
    app_key = str(current_user.get("app_key") or "").strip()
    query = {}
    if current_user.get("role") != "super_admin":
        query["tenant_id"] = tenant_id
        if app_key:  # ❌ CONDITIONAL: only applies if app_key exists
            query["app_key"] = app_key
    # Returns users from ALL app_keys if app_key is missing/empty
```

#### Impact
- **Scope Leakage:** If a user's token lacks an app_key field, they can list users from all app_keys
- **Tenant Listing:** Non-admin users with missing app_key can discover other tenants' users
- **Cross-Tenant Visibility:** Violates the three-part boundary (tenant_id, app_key, accounting_entity_id)

#### Attack Scenario
```python
# User with token: {"user_id": "u1", "tenant_id": "T123", "app_key": ""} (missing app_key)
# Query becomes: {"tenant_id": "T123"}
# Returns users from MandirMitra, GruhaMitra, MitraBooks, ALL apps for tenant T123
```

---

### 🟡 HIGH: Defaulting Behavior Instead of Strict Validation

**Location:** `app/modules/housing_compat/service.py:89-197`
**Severity:** HIGH
**Type:** Implicit Scope Assumption

#### The Issue
Throughout housing_compat/service.py, functions silently default to `app_key="gruhamitra"` instead of requiring it:

```python
# Lines 104, 130, 141, 157, etc. (VULNERABLE PATTERN)
async def create_member(*, tenant_id: str, app_key: str, payload: MemberCreateRequest) -> dict:
    # ...
    "app_key": str(app_key or "gruhamitra").strip(),  # ❌ Defaults to gruhamitra
```

#### Impact
- **Silent Scope Assumption:** If app_key=None is passed, operations silently default to gruhamitra
- **Backward Compatibility Trade-off:** While this prevents crashes on old code, it masks caller errors
- **Error Hiding:** Callers may accidentally omit app_key and get incorrect behavior without warning
- **Not Strict Enough:** Violates "always validate and fail if missing" principle

#### Current Pattern (PROBLEMATIC)
```python
# Caller forgets to pass app_key:
await create_member(tenant_id="T123", app_key=None, payload=...)
# Silently creates member in "gruhamitra" app_key instead of failing
```

---

## Vulnerability Assessment Matrix

| Vulnerability | Risk | Data Impact | Isolation Type | Affected Collections | Status |
|---|---|---|---|---|---|
| Missing app_key field | CRITICAL | Cross-tenant visibility | MongoDB document | housing_maintenance_collections | UNFIXED |
| Conditional app_key enforcement | CRITICAL | Scope bypass | Query filter | users (gruhamitra routes) | UNFIXED |
| Defaulting behavior | HIGH | Implicit scope | Service logic | all housing_compat tables | UNFIXED |

---

## What IS Working Well ✅

### housing_compat Module
The housing_compat module correctly implements three-part isolation:
- ✅ **Index Structure:** All collections have `[("tenant_id", 1), ("app_key", 1), ...]` indexes
- ✅ **Service Functions:** All functions require and use `app_key` parameter
- ✅ **Queries:** All MongoDB queries include `{"tenant_id": tenant_id, "app_key": app_key}`
- ✅ **Indexes Applied:** Lines 46-72 show proper app_key isolation

Example (CORRECT):
```python
# housing_compat/service.py:91-92 (CORRECT PATTERN)
occupancy = await members.find_one(
    {"tenant_id": tenant_id, "app_key": app_key, "flat_number": payload.flat_number, "status": "active"}
)
```

### Accounting Integration
- ✅ Maintenance collections post journal entries with `app_key="gruhamitra"`
- ✅ Double-entry principles correctly enforced
- ✅ Proper rollback on cross-DB write failure (line 74-75)

---

## Implementation Plan

### Phase 1: Critical Fixes (Blocking Launch) 🔴

#### Fix 1.1: Add app_key to housing_maintenance_collections

**File:** `app/modules/housing/service.py`

**Change Required:**
```python
# Line 21-42: Add app_key field and parameter
async def record_maintenance_collection(
    session: AsyncSession,
    *,
    tenant_id: str,
    app_key: str,  # ← ADD THIS PARAMETER
    created_by: str,
    payload: MaintenanceCollectionCreateRequest,
):
    collections = get_collection(MAINTENANCE_COLLECTIONS)
    collection_id = str(uuid4())
    doc = {
        "collection_id": collection_id,
        "tenant_id": tenant_id,
        "app_key": app_key,  # ← ADD THIS FIELD
        "amount": float(payload.amount),
        # ... rest unchanged
    }

# Line 15-18: Add app_key to indexes
async def ensure_maintenance_indexes() -> None:
    collections = get_collection(MAINTENANCE_COLLECTIONS)
    await collections.create_index([("tenant_id", 1), ("app_key", 1), ("collected_on", -1)])
    await collections.create_index([("tenant_id", 1), ("app_key", 1), ("collection_id", 1)], unique=True)
```

**Impact Analysis:**
- Callers in `app/modules/housing/router.py:24` must pass `app_key`
- Router must resolve app_key from user context or X-App-Key header
- All queries to housing_maintenance_collections now require (tenant_id, app_key)

---

#### Fix 1.2: Enforce app_key in /users/ Endpoint

**File:** `app/api/gruhamitra_compat_router.py`

**Change Required:**
```python
# Line 92-115: Make app_key enforcement strict
@router.get("/users/")
async def users_list_compat_endpoint(current_user: dict = Depends(get_current_user)):
    _require_role_admin(current_user)
    tenant_id = str(current_user.get("tenant_id") or "").strip()
    app_key = str(current_user.get("app_key") or "").strip()

    # ❌ OLD: Conditional enforcement
    # if app_key:
    #     query["app_key"] = app_key

    # ✅ NEW: Strict enforcement for non-superadmin
    if current_user.get("role") != "super_admin":
        if not app_key:
            raise HTTPException(
                status_code=400,
                detail="X-App-Key header or app_key in token required for non-admin users"
            )
        query = {
            "tenant_id": tenant_id,
            "app_key": app_key
        }
    else:
        query = {"tenant_id": tenant_id}  # superadmin can list all

    users = await get_collection("users").find(query).sort("full_name", 1).to_list(length=500)
    # ... rest unchanged
```

**Impact Analysis:**
- Non-admin users will receive 400 error if app_key is missing
- Prevents accidental scope bypass
- Superadmin can still query across app_keys (intentional)

---

#### Fix 1.3: Strict app_key Validation in housing/router.py

**File:** `app/modules/housing/router.py`

**Change Required:**
```python
# Line 14-35: Resolve app_key from user context
from app.core.tenants.app_resolvers import resolve_gruha_tenant

@router.post("/maintenance-collections", response_model=MaintenanceCollectionCreateResponse)
async def create_maintenance_collection(
    payload: MaintenanceCollectionCreateRequest,
    session: AsyncSession = Depends(get_async_session),
    current_user: dict = Depends(get_current_user),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    app_key = resolve_gruha_tenant(current_user, x_app_key)  # ← Strict resolution

    # app_key is now guaranteed to be non-empty
    try:
        collection = await record_maintenance_collection(
            session,
            tenant_id=tenant_id,
            app_key=app_key,  # ← Pass app_key explicitly
            created_by=current_user.get("sub", "system"),
            payload=payload,
        )
```

---

### Phase 2: Code Quality Improvements (Post-Launch)

#### Fix 2.1: Explicit Validation Instead of Defaults

**Pattern Change:**
```python
# ❌ OLD (implicit default):
"app_key": str(app_key or "gruhamitra").strip()

# ✅ NEW (explicit):
if not app_key:
    raise ValueError("app_key is required")
"app_key": str(app_key).strip()
```

**Scope:** Apply across `app/modules/housing_compat/service.py` in all service functions

---

## Testing Strategy

### Unit Tests Required

#### Test 1: housing_maintenance_collections has app_key
```python
# Tests: ensure_maintenance_indexes() creates correct indexes
# Verify: index includes ("tenant_id", 1), ("app_key", 1)
# Verify: insert fails if app_key is missing and indexes enforced
```

#### Test 2: record_maintenance_collection enforces app_key
```python
# Call: record_maintenance_collection(..., app_key="gruhamitra", ...)
# Verify: inserted document includes app_key field
# Verify: Two different app_keys create separate documents for same tenant
```

#### Test 3: /users/ endpoint enforces app_key
```python
# Call: GET /users/ with missing app_key (non-admin user)
# Verify: returns 400 Bad Request
# Verify: superadmin can still list all users
```

#### Test 4: housing/router.py passes app_key to service
```python
# Call: POST /housing/maintenance-collections with X-App-Key header
# Verify: record_maintenance_collection receives app_key parameter
# Verify: Maintenance record includes app_key in MongoDB
```

---

### Integration Tests Required

#### Test 5: Cross-tenant isolation in housing_maintenance_collections
```python
# Setup: Two tenants T1 and T2, both with GruhaMitra (app_key="gruhamitra")
# Action: Record maintenance collection in T1
# Query: Select from housing_maintenance_collections with (T2, gruhamitra)
# Verify: T1's maintenance not visible to T2
```

#### Test 6: Cross-app isolation within same tenant
```python
# Setup: Tenant T1 with both MandirMitra and GruhaMitra
# Action: Record maintenance in T1/gruhamitra
# Query: Query T1 with app_key="mandirmitra"
# Verify: Maintenance collection NOT visible (different app_key)
```

---

## Verification Checklist

### Before Launch ✅

- [ ] Fix 1.1: app_key added to housing_maintenance_collections documents
- [ ] Fix 1.1: Indexes updated with (tenant_id, app_key) boundary
- [ ] Fix 1.2: /users/ endpoint enforces strict app_key for non-admins
- [ ] Fix 1.3: housing/router.py resolves and passes app_key
- [ ] All unit tests pass (Test 1-4)
- [ ] All integration tests pass (Test 5-6)
- [ ] GitNexus impact analysis shows no unexpected side effects
- [ ] No regressions in existing GruhaMitra routes
- [ ] Load test: maintenance collection recording works at scale

### Post-Launch (Phase 2)

- [ ] Apply defaulting → explicit validation pattern across housing_compat
- [ ] Code review: Verify all service functions require app_key parameter
- [ ] Security audit: Third-party review of multi-tenancy boundaries

---

## Risk Assessment

### High Risk ⚠️
- housing_maintenance_collections missing app_key → **DATA ISOLATION BYPASS**
- /users/ endpoint conditional enforcement → **SCOPE LEAKAGE**

### Medium Risk ⚠️
- Defaulting pattern in service functions → **IMPLICIT SCOPE ASSUMPTION**

### Recommended Action
✅ **Fix all issues before production launch**
The three-part isolation boundary (tenant_id, app_key, accounting_entity_id) is foundational to the multi-tenancy model. Any gaps create data exfiltration risk.

---

## Appendix: Affected Code Locations

| File | Issue | Lines | Severity |
|---|---|---|---|
| app/modules/housing/service.py | Missing app_key field | 31-42 | CRITICAL |
| app/modules/housing/service.py | Missing app_key in indexes | 15-18 | CRITICAL |
| app/modules/housing/router.py | Not passing app_key to service | 24 | CRITICAL |
| app/api/gruhamitra_compat_router.py | Conditional app_key enforcement | 98-101 | CRITICAL |
| app/modules/housing_compat/service.py | Defaulting app_key behavior | multiple | HIGH |

---

## Related Documentation

- **AGENTS.md** - Multi-tenancy safety rules and isolation requirements
- **CLAUDE.md** - Security review procedures and impact analysis
- **Phase 4 Completion Status** - Prior app_key isolation work (MandirMitra)

---

**Report Generated:** 2026-04-30
**Status:** Ready for implementation
**Next Step:** Run `gitnexus_impact()` on affected functions before patching
