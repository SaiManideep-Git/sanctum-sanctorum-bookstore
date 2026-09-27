# Sanctum Sanctorum Bookstore — Project Notes

## 1. Deployment
- **Live URL**: [Add your live URL here once deployed, e.g. https://sanctum-bookstore.onrender.com]
- **Notes to test**: The app runs with default seed data. You can access the API documentation at `/docs` or the web interface at `/`.

---

## 2. What I Finished
I was able to complete all the requirements across the entire application. All **202 tests** in the pytest suite are passing:
- [x] **Books**: ISBN-13 checksum validation, duplicate checking (409), partial updates with PATCH, search filtering by title/author, price range filtering, sorting, and pagination.
- [x] **Members**: Input validation, case-insensitive email normalization and duplicate check, member lookup.
- [x] **Orders**: Sequential validation checks (422, 404, 403, 409), tier and bulk discounts, atomic stock reservation, paying orders, and restoring stock on cancellation.
- [x] **Loans**: Completed the Loan database model, implemented borrowing rules (tier limits, overdue loan checks, duplicate book checks), loan return logic, and dynamic late fee calculations.
- [x] **Member Stats & Reports**: Member activity summary for paid orders/loans, and top-selling books aggregation query.

---

## 3. How I Built It (Step-by-Step)

I usually build backends using the **MERN stack** (Node.js, Express, MongoDB), so working with Python, FastAPI, and SQLAlchemy was a new experience. But since core backend principles (routing, validation, database transactions, status codes) are the same, I picked up FastAPI while reading the code and tests.

Here is the step-by-step breakdown of what I did:

### Step 1: Books
- **ISBN-13 Checksum**: The starter code was stripping hyphens, but wasn't verifying the 13th check digit. I wrote the algorithm in `app/schemas.py` using alternating weights of 1 and 3, calculating `(10 - sum % 10) % 10`. If it doesn't match, it raises a `ValueError` which FastAPI automatically turns into a 422 error.
- **Duplicate ISBN**: SQLite was throwing a 500 database error when inserting an existing ISBN. I added a check in `app/services/books.py` to query for the ISBN first and return HTTP 409 Conflict if found.
- **PATCH /books/{id}**: The router was missing the PATCH endpoint and the service function was throwing `NotImplementedError`. I added the route in `routers/books.py` and used Pydantic's `data.model_dump(exclude_unset=True)` in `services/books.py` so only fields sent in the request are updated, while `isbn` is ignored.
- **Search & Pagination**: In `list_books`, I updated the search query using SQLAlchemy's `or_` so `q` searches both title and author. I also made sure the `total` count is calculated on the query before applying `limit` and `offset`, so the pagination count is always accurate.

### Step 2: Members
- **Email Normalization**: In `MemberCreate`, the validator was checking regex before stripping and lowercasing the email. I fixed it to strip spaces and convert to lowercase first so emails like `"  User@Domain.COM  "` are cleaned and saved properly.
- **Duplicate Email**: Added a query in `services/members.py` to check for existing emails and return 409 Conflict. Because emails are normalized to lowercase on input, case-insensitive uniqueness worked naturally.

### Step 3: Orders
- **Order Validation**: In `app/schemas.py`, I added a validator on `OrderCreate` to reject empty item lists and reject duplicate `book_id`s with 422.
- **Strict Order of Checks**: In `services/orders.py`, I made sure the checks happen in the exact sequence requested by the spec:
  1. 404 if member is missing, or if any requested book is missing.
  2. 403 if any book is restricted and the member's tier is below `master`. (I also fixed a small bug in `tier_at_least` where it was using `>` instead of `>=`).
  3. 409 if any book lacks enough stock.
- **Atomic Stock Reservation**: If any check fails, no stock is changed. When all checks pass, stock is decremented immediately for all books.
- **Discounts**: Tier discount + an additional 5% bulk discount if the total quantity across all items is 10 or more. I used integer division (`// 100`) to round discount cents down.
- **Cancel Order**: When an order is cancelled, I added a loop to restore the reserved quantities back to each book's stock.

### Step 4: Loans
- **Completing the Model**: In `app/models.py`, the `Loan` table was missing columns. I added `due_at`, `returned_at` (nullable), and `late_fee_cents`.
- **Borrowing Rules**: Before creating a loan, the service checks that the member exists, book exists, tier allows restricted books, the member has no overdue loans, the member doesn't already have that book borrowed, and they haven't reached their tier limit.
- **Returning & Late Fees**: When returning a book, stock is incremented by 1. If returned past `due_at`, the late fee is calculated using `math.ceil` on the elapsed days multiplied by 25 cents, capped at the book's retail price.

### Step 5: Member Stats & Reports
- **Member Stats**: Implemented `get_member_stats` to count paid orders and total spent, active unreturned loans, overdue loans (where `now > due_at`), and total late fees collected.
- **Top Books Report**: In `services/reports.py`, I wrote an aggregated SQL query joining `books`, `order_items`, and `orders`, filtering only `paid` orders, grouping by book, and sorting by `copies_sold` descending and title ascending.

---

## 4. Key Architectural Decisions & Trade-offs

1. **Keeping Routers Thin**:
   I kept all route handlers in `app/routers/` very small (just taking the request, calling the service, and returning the result). All database queries, status code decisions, and business rules live strictly in `app/services/`. This keeps the code clean and easy to test.

2. **Validation in Schemas vs Services**:
   I used Pydantic schemas (`app/schemas.py`) for field-level validations (checking string lengths, email regex, ISBN checksums, and duplicate items in orders). This ensures bad input is stopped at the front door with HTTP 422 before any database queries run.

3. **Data Integrity on Stock**:
   For orders, I checked the stock of *all* items in the request before deducting any stock. If book #3 is out of stock, book #1 and #2 are never touched. This ensures orders are strictly all-or-nothing.

---

## 5. Spec Observations & Edge Cases Handled

- **Strict Precedence of Error Codes in Orders**: 
  The specification mandates a strict sequence of checks on `POST /orders`:
  `422 (format/duplicate items)` $\rightarrow$ `404 (missing member/books)` $\rightarrow$ `403 (restricted tier access)` $\rightarrow$ `409 (insufficient stock)`.
  To respect this, I ensured all books are validated for existence before evaluating access permissions. For example, if an Apprentice orders both a restricted book and a non-existent book ID, the API correctly returns `404 Not Found` rather than prematurely throwing `403 Forbidden`.
- **Atomic Stock Reservation (All-or-Nothing)**:
  Order creation requires that if any single item lacks sufficient stock, no stock is changed and no order is persisted. I separated the logic into a pre-flight validation phase (checking all quantities against stock) before executing any inventory mutations, ensuring the database is never left in a partially updated state.
- **Strict Loan Boundary Conditions**:
  `SPEC.md` defines overdue loans strictly as `now > due_at`. At the exact timestamp of `due_at`, a loan is still considered active, owes \$0 in late fees, and does not block new borrowing. I ensured all comparison logic consistently uses `>` rather than `>=`.
- **Starter Bug in `tier_at_least`**:
  In `app/services/members.py`, the starter helper `tier_at_least` used a strict greater-than comparison (`index > minimum`). This caused `master` members to be incorrectly blocked from restricted books because their tier index equaled the minimum requirement. Updating this to `>=` resolved the access check accurately.

---

## 6. Optional Extras Completed

- **`GET /members` Endpoint with Pagination**:
  I implemented the optional paginated member listing endpoint (`GET /members`):
  - Created the `MemberPage` response schema containing `items`, `total`, `limit`, and `offset`.
  - Added query parameter validation using FastAPI's `Query(20, ge=1, le=100)` and `offset >= 0`.
  - In `services/members.py`, the query executes a `func.count()` on a subquery to calculate the true total member count *before* applying `limit` and `offset`, returning stable, ID-ordered paginated results.

---

## 7. AI Usage

- **Tools Used**: Gemini assistant in Antigravity.
- **How I Used It**:
  - Accelerated my transition from the MERN stack to Python/FastAPI by mapping familiar patterns (Express routing $\rightarrow$ APIRouter, Zod/Joi $\rightarrow$ Pydantic schemas, Mongoose $\rightarrow$ SQLAlchemy 2.0 ORM sessions).
  - Rubber-ducking architecture decisions to ensure routers remained thin and business logic stayed confined to the service layer.
  - Analyzing detailed pytest traceback logs during debugging sessions.
- **Where I Critically Reviewed & Overrode the AI**:
  - **Order Creation & Stock Atomicity**: When drafting `create_order`, the AI initially suggested a single loop that resolved books, checked stock, and decremented inventory simultaneously. I identified two critical issues with this suggestion:
    1. **Data Integrity Violation**: If the final item in a 5-item order failed the stock check, the earlier items would have already had their stock decremented in memory, violating the "all-or-nothing" requirement.
    2. **Status Code Precedence**: The single-loop approach failed to enforce the spec's requirement that all 404 checks must precede 403 checks, and all 403 checks must precede 409 checks.
    I rejected the AI's single-pass implementation and restructured `create_order` into two explicit phases: a validation phase that resolves all entities and verifies permissions and stock across all items, followed by a separate mutation phase that reserves stock and writes the order items only when every check passes.