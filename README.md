# DoseDeck

**AI-powered pharmacy inventory intelligence for independent pharmacies.**

Traditional pharmacy software tells you what happened. DoseDeck helps you decide what should happen next.

DoseDeck is a functional, local-first hackathon prototype. Stock-in, FEFO billing, batch quantities, expiry risk, forecasts, reorder recommendations, supplier comparisons, AI recommendations and procurement plans share one database; an action on one screen changes the next decision.

## Start in one command

```powershell
docker compose up --build
```

- Web app: <http://localhost:3000/?demo=true>
- Backend API: <http://localhost:8000>
- Swagger: <http://localhost:8000/docs>

Docker is optional. For the simplest local Windows run:

```powershell
pip install -r requirements.txt
.\run_local.ps1
```

Then open <http://127.0.0.1:8000/?demo=true>.

## Demo login

- Email: `demo@dosedeck.ai`
- Password: `demo123`
- Or select **Continue in Demo Mode**.

## What works

- Jury-grade dashboard populated from backend data
- 30+ medicines and 50+ batches with deterministic demo state
- Inventory search, filters and medicine detail intelligence
- AI/mock invoice extraction with structured Pydantic validation
- Missing-expiry recovery through medicine-pack scanning
- Confirmed purchase receiving and duplicate-invoice protection
- POS billing with FEFO allocation and printable receipts
- Transactional stock deduction, returns and movement ledger
- 7-, 14- and 30-day SKU forecasts with historical/forecast series
- Days-of-cover stockout prediction using supplier lead time
- Sell-through-based expiry risk and value-at-risk explanations
- Deterministic smart reorder quantities (LLMs do not do arithmetic)
- Weighted supplier comparison with transparent prototype scoring
- Multi-agent procurement plan with mandatory human approval
- Mock purchase-order generation; no external supplier order is sent
- Accept/dismiss AI recommendations
- Analytics and clearly labelled illustrative pilot metrics
- Repeatable **Reset Demo Data** action
- Offline demo operation after dependencies are installed

## Architecture

```text
Web / camera / invoice
          |
          v
Responsive DoseDeck SPA
          |
          v
FastAPI + Pydantic validation
          |
      +---+----------------------+----------------------+
      |                          |                      |
      v                          v                      v
Transaction services       Intelligence services    AI adapters
purchase / FEFO sale       forecast / expiry        mock (default)
returns / ledger           stockout / reorder       local placeholder
                           supplier scoring         hosted adapter
      |                          |                      |
      +--------------------------+----------------------+
                                 |
                                 v
                         SQLAlchemy persistence
                         SQLite / PostgreSQL-ready
```

The browser is deliberately build-free for demo reliability. The service boundary is API-first, so a Next.js client can replace it without changing core calculations or persistence.

## Intelligence design

The intelligence layer is not an LLM wrapper. It combines:

1. Verified transaction and batch-level inventory data
2. Deterministic synthetic sales history for presentation mode
3. Weighted moving-average demand profiles and backtest metadata
4. Days-of-cover and lead-time stockout logic
5. Forecast sell-through expiry-risk calculations
6. Replenishment-cycle and safety-stock arithmetic
7. Transparent supplier scoring
8. Vision/OCR adapters for invoice and medicine-pack capture
9. Agent orchestration with a required human approval gate

Core functions live in `backend/services/intelligence.py`; API composition lives in `backend/intelligence_api.py`; transaction integrity remains in `backend/main.py`.

## AI modes

Copy `.env.example` to `.env` when you want to change configuration.

```env
AI_MODE=auto
DEMO_MODE=true
PRESENTATION_MODE=true
```

### `AI_MODE=auto` (default)

Uses the configured Groq/Qwen vision model when `GROQ_API_KEY` is present. Without a key, it safely falls back to deterministic mock extraction.

To force live extraction, add `AI_MODE=groq` to `.env`, restart the server, and upload a clear JPG or PNG image of the invoice. The current live Groq path accepts image invoices; convert PDFs to an image before uploading.

### `AI_MODE=mock`

No API key or model download is required. Invoice and pack scanning return deterministic, realistic structured data after a short loading state. All values still pass Pydantic validation and require confirmation.

### `AI_MODE=local`

This is the provider boundary for a local OCR/vision deployment. Connect PaddleOCR for text regions and Qwen2.5-VL through Hugging Face Transformers for structured visual interpretation. Keep the returned schema identical to `InvoiceVisionResult` and `MedicineVisionResult`, and never update inventory from raw model output.

The legacy hosted adapter can also be configured with the environment variables shown in `.env.example`. Demo mode does not need it.

## Deterministic jury walkthrough

1. Open the dashboard. It starts with ₹4,82,350 inventory value, ₹28,450 today's sales, ₹18,760 elevated expiry risk, 12 stockout risks and 18 reorder recommendations.
2. Open **Smart Reorder**, find Dolo 650 and select **Why?**. The initial state is stock 12, predicted 14-day demand 44, about four days of cover and a 45-unit recommendation.
3. Select Dolo's recommended supplier to see price, lead time, fill rate and risk-adjusted scoring.
4. Select recommendations and create a procurement plan. Review the agent trace, then approve to generate mock POs.
5. Open **Stock In** and choose the pre-built demo invoice. The mock AI extracts three lines; Pan 40 is intentionally missing an expiry.
6. Select **Scan pack** on Pan 40. The validated mock pack result fills batch `PN9826` and expiry `FEB 2028`.
7. Confirm the purchase. The database receives 110 units and the inventory view updates.
8. Open **Billing**, search Dolo 650, add it and generate a bill. The earliest valid batch is allocated first.
9. Return to the dashboard or Smart Reorder to see the changed stock and decision signals.
10. Use **Settings → Reset Demo Data** before the next jury run.

Sample visual assets are in `sample_invoices/`. The older CSV importer remains available through the API and `sample_invoice.csv`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The test suite covers:

- Purchase increases stock
- Sale decreases stock
- FEFO selects the earliest valid batch
- Expired incoming stock is rejected
- Duplicate invoices are rejected
- Sale returns restore the original batch
- Reconciliation creates ledger movements
- Forecast and reorder endpoints
- Reorder quantity is non-negative
- Expiry risk calculation
- Supplier scoring/ranking
- Mock invoice schema validation
- Procurement requires human approval
- Full invoice → pack scan → purchase → inventory → billing loop

## API highlights

- `GET /api/dashboard`
- `GET /api/products` and `GET /api/products/{id}`
- `GET /api/inventory`
- `POST /api/purchases/confirm`
- `POST /api/ai/invoice/extract`
- `POST /api/ai/batch-expiry/extract`
- `POST /api/sales/complete`
- `GET /api/expiry-risk`
- `GET /api/forecasts/{product_id}`
- `GET /api/stockout-risk`
- `GET /api/reorder`
- `POST /api/reorder/plan`
- `GET /api/suppliers/compare/{product_id}`
- `GET /api/ai/recommendations`
- `POST /api/purchase-orders`
- `POST /api/demo/reset`

## Responsible AI boundary

DoseDeck manages pharmacy business operations. It does not diagnose patients, recommend prescription medicines, suggest substitutions, provide dosages or generate treatment advice.

**DoseDeck AI provides inventory and procurement decision support. Final operational decisions remain with the pharmacy.**
