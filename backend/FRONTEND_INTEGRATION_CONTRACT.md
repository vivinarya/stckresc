# StockRescue Frontend Integration Contract

This contract defines the authoritative HTTP API surface, authentication requirements, schemas, operational behavior, and security boundaries implemented in the StockRescue FastAPI Warehouse backend (`C:\agent\stockrescue\backend`).

Frontend client developers must build directly against these validated specifications.

---

## 1. Base URL & Network Configuration

- **Default development Base URL:** `http://127.0.0.1:8000` (or configured via Vite environment variable `VITE_API_BASE_URL`).
- **CORS configuration:** Handled via backend `CORS_ORIGINS` setting (defaults to `http://localhost:5173,http://127.0.0.1:5173`). Credentials (`Access-Control-Allow-Credentials`) are disabled.
- **Request payload limit:** 1 MB (`1_048_576` bytes default). Payloads exceeding this return HTTP 413.

---

## 2. Authentication & Authorization Architecture

The backend supports two distinct authentication paths:

1. **Bearer JWT (Authoritative Supabase Auth):**
   - Header: `Authorization: Bearer <access_token>`
   - Validated server-side using ES256 (or HS256 fallback) with JWKS caching and strict audience (`authenticated`) and issuer validation.
   - User identity (`sub`), email, and roles are extracted by the backend.
   - **Role authorization:** Protected approval endpoints require the `procurement_approver` or `admin` role in `app_metadata.roles` or `user_metadata.roles`. The browser/client cannot claim or elevate roles.

2. **X-API-Key (Backend-to-Backend / Local Operations):**
   - Header: `X-API-Key: <32-512 char key>`
   - Constant-time HMAC comparison.

3. **Local Loopback Fallback (Development Mode):**
   - Requests from `127.0.0.1` or `::1` without headers are permitted for non-role-restricted endpoints when `WAREHOUSE_API_KEY` is not set.

---

## 3. Authoritative API Endpoints & Schemas

### 3.1 System Health

#### `GET /health`
- **Description:** Verifies service availability, demo mode, and purchasing status.
- **Auth:** None.
- **Response `200 OK`:**
  ```json
  {
    "app_name": "StockRescue Warehouse API",
    "demo_mode": true,
    "real_purchasing_enabled": false
  }
  ```

---

### 3.2 Disruption Cases

#### `POST /cases`
- **Description:** Creates and records a new disruption case intake.
- **Auth:** Bearer JWT or API key.
- **Request Body (`CaseCreateRequest`):**
  - `case_id`: String (1-100 chars, regex `^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$`)
  - `title`: String (1-200 chars)
  - `affected_sku`: String (1-64 chars)
  - `failed_supplier_id`: String (1-100 chars)
  - `currency`: String (3-letter ISO code, e.g., `"INR"`, `"USD"`, `"EUR"`)
  - `budget`: Optional Decimal string/number
  - `reported_at`: Optional ISO 8601 UTC timestamp
  - `delayed_until`: Optional ISO 8601 UTC timestamp
  - `inventory`: Array of `InventoryRecord` (`sku`, `quantity_on_hand`, `quantity_committed`, `location`)
  - `jobs`: Array of `JobRequirement` (`job_id`, `sku`, `quantity_required`, `required_by`, `priority`, `description`)
  - `offers`: Array of `SupplierOffer` (`offer_id`, `supplier_id`, `supplier_name`, `sku`, `quantity_available`, `unit_price`, `freight_cost`, `minimum_order_quantity`, `pack_size`, `lead_time_days`, `quoted_at`, `valid_until`, `currency`)
  - `compatibility`: Array of `CompatibilityAssessment` (`requested_sku`, `candidate_sku`, `status`, `evidence`, `assessed_by`, `assessed_at`)
- **Response `201 Created`:** Complete validated `DisruptionCase` object.
- **Errors:** `400` (Validation), `409` (Conflict/Duplicate ID).

#### `GET /cases`
- **Description:** Lists disruption cases with pagination.
- **Query params:** `limit` (int, 1-100, default 50), `offset` (int, default 0).
- **Response `200 OK` (`CaseListResponse`):**
  ```json
  {
    "items": [...],
    "count": 12,
    "limit": 50,
    "offset": 0
  }
  ```

#### `GET /cases/{case_id}`
- **Description:** Retrieves full disruption case details by ID.
- **Response `200 OK`:** `DisruptionCase`.
- **Errors:** `404` (Not Found).

---

### 3.3 Recovery Planning

#### `POST /cases/{case_id}/plans`
- **Description:** Runs deterministic optimization to generate and persist a recovery plan for the case.
- **Query params:** `as_of` (optional ISO 8601 timestamp).
- **Response `201 Created`:** `RecoveryPlan` with status:
  - `proposed`: Feasible plan ready for operator review.
  - `no_feasible_plan`: Mathematical proof that constraints (cost, timing, stock, compatibility) could not be met.
- **Errors:** `404` (Case Not Found), `409` (Plan exists).

#### `GET /cases/{case_id}/plans`
- **Description:** Lists all recovery plans generated for the case.
- **Response `200 OK` (`PlanListResponse`):** Array of `RecoveryPlan` items and total count.

---

### 3.4 Procurement Approvals (Human-in-the-Loop)

#### `POST /cases/{case_id}/plans/{plan_id}/approvals`
- **Description:** Authorizes a proposed recovery plan.
- **Auth:** Requires `procurement_approver` or `admin` role.
- **Request Body (`ApprovalCreateRequest`):**
  - `notes`: String (1-500 chars).
  - `expires_in_hours`: Optional integer (1-168, default 24).
- **Security enforcement:**
  - Plan must exist, belong to `case_id`, and be in `proposed` status.
  - Server computes canonical SHA-256 plan fingerprint (`plan_fingerprint`) and locks the approval to that exact plan state.
  - Approver identity is resolved from trusted JWT claims (`sub` / `email`), never the client payload.
- **Response `201 Created`:** `ProcurementApproval`.

#### `GET /cases/{case_id}/plans/{plan_id}/approvals`
- **Description:** Lists all approvals (historical, active, and revoked) for the plan.
- **Response `200 OK` (`ApprovalListResponse`):** List of approvals.

#### `GET /cases/{case_id}/plans/{plan_id}/approvals/active`
- **Description:** Retrieves the single current, unrevoked, unexpired approval matching the exact plan fingerprint.
- **Response `200 OK`:** `ProcurementApproval` or `null`.

#### `POST /cases/{case_id}/plans/{plan_id}/approvals/{approval_id}/revoke`
- **Description:** Revokes an existing approval.
- **Auth:** Requires `procurement_approver` or `admin` role.
- **Request Body (`ApprovalRevokeRequest`):**
  - `revocation_reason`: String (1-300 chars).
- **Response `200 OK`:** Updated `ProcurementApproval` with `revoked: true`.

#### `GET /cases/{case_id}/plans/{plan_id}/approvals/audit`
- **Description:** Lists tamper-evident audit events recorded for plan approvals and revocations.
- **Response `200 OK` (`AuditEventListResponse`):** List of `AuditEvent`.

---

### 3.5 Delivery Monitoring

#### `POST /cases/{case_id}/plans/{plan_id}/delivery-events`
- **Description:** Records an external delivery tracking event (from carrier webhook, operator input, etc.).
- **Request Body (`DeliveryEventCreateRequest`):**
  - `status`: `"ordered" | "confirmed" | "in_transit" | "out_for_delivery" | "delivered" | "delayed" | "cancelled"`
  - `occurred_at`: ISO 8601 UTC timestamp.
  - `source`: `"carrier_api" | "authorized_operator" | "simulator"`
  - `reference`: Optional string (carrier tracking number).
  - `details`: Optional description string.
- **Response `201 Created`:** Saved `DeliveryEvent`.

#### `GET /cases/{case_id}/plans/{plan_id}/delivery-events`
- **Description:** Lists delivery events recorded for the plan.
- **Response `200 OK` (`DeliveryEventListResponse`).

#### `GET /cases/{case_id}/plans/{plan_id}/delivery-status`
- **Description:** Authoritative delivery assessment from `agents.delivery_watch`.
- **Query params:** `as_of` (optional timestamp), `allow_simulated` (boolean, default false).
- **Response `200 OK` (`DeliveryWatchResult`):**
  - `status`: `"unknown" | "in_progress" | "delivered" | "delayed" | "cancelled" | "overdue" | "review_required"`
  - `latest_event_id`: String or null
  - `latest_event_at`: Timestamp or null
  - `expected_completion_at`: Timestamp or null
  - `alerts`: List of alert strings (e.g., `["delivery_deadline_passed"]`)
  - `accepted_event_count`: Integer count of verified non-conflicting events.
  - *Crucial UI rule:* When events are empty, `status` is strictly `"unknown"`. Never represent absence of events as delivered.

---

## 4. Frontend Security & Safety Mandates

1. **Purchasing Boundary:** Live purchasing and automated vendor messaging are permanently disabled (`real_purchasing_enabled: false`). The frontend must label all purchase orders and vendor communications as **Unsent Drafts** requiring human review.
2. **Untrusted Client Claims:** Never send flags like `approval_verified: true` to bypass verification; the backend validates approvals against persisted cryptographic plan fingerprints and database records.
3. **No Secret Exposure:** Frontend build artifacts, environment variables, source maps, and network calls must never contain agent seeds, Langfuse secret keys, Supabase service-role keys, or database connection strings. Only publish `VITE_API_BASE_URL` and `VITE_SUPABASE_ANON_KEY` / `VITE_SUPABASE_URL`.
