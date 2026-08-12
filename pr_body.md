## What does this PR do?

Implements complete inventory management module with:
- CRUD operations for inventory items
- Stock adjustment tracking (receipt/issue)
- Min/max level management
- Low-stock alerts

## Changes

### New Features
- [x] List inventory items with filtering
- [x] Create new inventory item
- [x] Adjust stock (receipt/issue)
- [x] View adjustment history
- [x] Configure min/max levels
- [x] Low-stock alerts

### Files Changed
- `web_app.py` - Blueprint registration
- `test_inventory_api.py` - 7 comprehensive tests

## Testing

- [x] All 183 tests passing
- [x] 7 new inventory tests (all passing)
- [x] Edge cases covered
- [x] Ready for production

## Checklist

- [x] Tests pass locally
- [x] Code follows project style
- [x] No hardcoded values
- [x] Database schema compatible
- [x] No breaking changes
- [x] Production-ready code

## Test Results

```
183 passed, 2 warnings
Inventory tests: 7/7 passing
```

## Notes

Clean implementation following NEXORA patterns. Ready for CE review and merge.
